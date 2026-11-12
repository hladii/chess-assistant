import atexit
import os
import secrets
import threading
import webbrowser
from datetime import datetime, timedelta

import chess
import chess.svg
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from assistant import (
    TRAINER_ELO_LEVELS,
    analyze_position,
    board_from_saved_history,
    engine_reply,
    evaluate_user_move,
    find_engine,
    game_over_message,
    parse_history,
)
from auth import (
    current_user_email,
    current_user_id,
    get_csrf_token,
    is_authenticated,
    login_required,
    login_user,
    logout_user,
    validate_csrf,
    validate_email,
    validate_new_password,
    verify_password,
)
from db import (
    create_game,
    delete_game,
    get_game_for_user,
    get_user_by_email,
    get_user_by_id,
    init_db,
    list_games,
    update_game_history,
    update_user_password,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') in ('1', 'true', 'True'),
)

init_db()

_engine = None
_engine_lock = threading.Lock()


def _shutdown_engine():
    global _engine
    engine = _engine
    _engine = None
    if engine is not None:
        try:
            engine.quit()
        except Exception:
            pass


def get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = find_engine()
                atexit.register(_shutdown_engine)
    return _engine


@app.context_processor
def inject_auth():
    return {
        'csrf_token': get_csrf_token(),
        'current_user_email': current_user_email(),
        'is_authenticated': is_authenticated(),
    }


def side_label(color):
    return 'White' if color == chess.WHITE else 'Black'


def color_code(color):
    return 'w' if color == chess.WHITE else 'b'


def parse_color(value):
    return chess.WHITE if str(value).lower() in ('w', 'white') else chess.BLACK


def format_move_history(board, start_fen):
    replay = chess.Board(start_fen)
    parts = []
    for move in board.move_stack:
        if replay.turn == chess.WHITE:
            parts.append(f'{replay.fullmove_number}.')
        parts.append(replay.san(move))
        replay.push(move)
    return ' '.join(parts)


def starting_fen_from_board(board):
    replay = board.copy()
    while replay.move_stack:
        replay.pop()
    return replay.fen()


def history_preview(history, limit=72):
    text = (history or '').strip()
    if not text:
        return 'No moves yet'
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def board_from_game(game):
    start_fen = game.get('start_fen') or chess.STARTING_FEN
    return board_from_saved_history(start_fen, game.get('history') or ''), start_fen


def build_suggestions(board, user_color):
    if board.is_game_over() or board.turn != user_color:
        return []
    engine = get_engine()
    with _engine_lock:
        raw = analyze_position(engine, board, user_color)
    return [
        {
            **option,
            'first_move': option['moves'][0] if option['moves'] else '',
        }
        for option in raw
    ]


def board_svg(board, user_color):
    lastmove = board.peek() if board.move_stack else None
    check = board.king(board.turn) if board.is_check() else None
    return chess.svg.board(
        board=board,
        orientation=user_color,
        lastmove=lastmove,
        check=check,
        size=420,
    )


def is_trainer_game(game):
    return (game.get('mode') or 'assistant') == 'trainer'


def game_payload(game, board, start_fen, suggestions=None, message='', error='', analyze=False, coach=None):
    user_color = parse_color(game['user_color'])
    ended = game_over_message(board)
    if ended:
        message = ended
    your_turn = board.turn == user_color and not ended
    trainer = is_trainer_game(game)
    # In trainer mode the engine replies automatically — never ask for opponent SAN.
    if trainer and not your_turn and not ended:
        move_prompt = 'Trainer thinking…'
    else:
        move_prompt = 'Your move' if your_turn else 'Opponent move'
    if suggestions is None:
        # Skip heavy multipv suggestions in trainer to save CPU on small VMs.
        if trainer:
            suggestions = []
        else:
            suggestions = build_suggestions(board, user_color) if analyze and your_turn else []
    return {
        'started': True,
        'game_id': game['id'],
        'name': game['name'],
        'mode': game.get('mode') or 'assistant',
        'trainer_elo': game.get('trainer_elo'),
        'user_side': side_label(user_color),
        'side_to_move': side_label(board.turn),
        'your_turn': your_turn,
        'move_prompt': move_prompt,
        'suggestions': suggestions,
        'message': message,
        'error': error,
        'coach': coach,
        'board_svg': board_svg(board, user_color),
        'game_over': bool(ended),
        'move_count': len(board.move_stack),
        'history': format_move_history(board, start_fen),
        'csrf_token': get_csrf_token(),
    }


def maybe_play_trainer_opening(game, board, start_fen):
    """If user is Black in a fresh trainer game, Stockfish plays White's first move."""
    if not is_trainer_game(game):
        return board, None
    user_color = parse_color(game['user_color'])
    if board.turn == user_color or board.is_game_over() or board.move_stack:
        return board, None
    elo = game.get('trainer_elo') or 1200
    engine = get_engine()
    with _engine_lock:
        san = engine_reply(engine, board, elo)
    if not san:
        return board, None
    board.push_san(san)
    update_game_history(game['id'], current_user_id(), format_move_history(board, start_fen))
    return board, san


def require_owned_game(game_id):
    user_id = current_user_id()
    game = get_game_for_user(game_id, user_id)
    if not game:
        return None, None, None
    board, start_fen = board_from_game(game)
    return game, board, start_fen


@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_authenticated():
        return redirect(url_for('index'))

    error = ''
    if request.method == 'POST':
        if not validate_csrf():
            error = 'Invalid session token. Refresh and try again.'
        else:
            email, email_error = validate_email(request.form.get('email', ''))
            password = request.form.get('password') or ''
            if email_error:
                error = email_error
            else:
                user = get_user_by_email(email)
                if not user or not verify_password(user['password_hash'], password):
                    error = 'Invalid email or password.'
                else:
                    login_user(user)
                    next_url = request.args.get('next') or url_for('index')
                    if not next_url.startswith('/'):
                        next_url = url_for('index')
                    return redirect(next_url)

    return render_template('login.html', error=error, csrf_token=get_csrf_token())


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    if not validate_csrf():
        flash('Invalid session token.', 'error')
        return redirect(url_for('index'))
    logout_user()
    return redirect(url_for('login'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_user_by_id(current_user_id())
    message = ''
    error = ''

    if request.method == 'POST':
        if not validate_csrf():
            error = 'Invalid session token. Refresh and try again.'
        else:
            current_password = request.form.get('current_password') or ''
            new_password = request.form.get('new_password') or ''
            confirm_password = request.form.get('confirm_password') or ''
            if not verify_password(user['password_hash'], current_password):
                error = 'Current password is incorrect.'
            else:
                pwd_error = validate_new_password(new_password, confirm_password)
                if pwd_error:
                    error = pwd_error
                elif verify_password(user['password_hash'], new_password):
                    error = 'New password must be different from the current one.'
                else:
                    update_user_password(user['id'], new_password)
                    message = 'Password updated.'
                    user = get_user_by_id(user['id'])

    return render_template(
        'profile.html',
        user=user,
        message=message,
        error=error,
        csrf_token=get_csrf_token(),
    )


def format_updated_at(value):
    """Human-readable timestamp for list cards."""
    if not value:
        return ''
    text = str(value).strip()
    try:
        # Accept both "...+00:00" and "...Z"
        normalized = text.replace('Z', '+00:00')
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime('%d.%m.%Y · %H:%M')
    except ValueError:
        return text[:16].replace('T', ' ')


def annotate_games(games):
    for game in games:
        game['side_label'] = side_label(parse_color(game['user_color']))
        game['history_preview'] = history_preview(game['history'], limit=56)
        game['updated_label'] = format_updated_at(game.get('updated_at'))
        mode = game.get('mode') or 'assistant'
        if mode == 'trainer':
            game['mode_label'] = f"Тренер · {game.get('trainer_elo') or '?'} Elo"
        else:
            game['mode_label'] = 'Асистент'
    return games


def list_endpoint_for_mode(mode):
    if mode == 'trainer':
        return 'training_list'
    return 'games_list'


@app.route('/')
@login_required
def index():
    return render_template('home.html')


@app.route('/games', methods=['GET'])
@login_required
def games_list():
    games = [
        g for g in list_games(current_user_id())
        if (g.get('mode') or 'assistant') != 'trainer'
    ]
    annotate_games(games)
    return render_template(
        'games.html',
        games=games,
        error=request.args.get('error', ''),
    )


@app.route('/training', methods=['GET'])
@login_required
def training_list():
    games = [
        g for g in list_games(current_user_id())
        if (g.get('mode') or 'assistant') == 'trainer'
    ]
    annotate_games(games)
    return render_template(
        'training.html',
        games=games,
        trainer_elo_levels=TRAINER_ELO_LEVELS,
        error=request.args.get('error', ''),
    )


@app.route('/games', methods=['POST'])
@login_required
def create():
    mode = (request.form.get('mode') or 'assistant').strip().lower()
    if mode not in ('assistant', 'trainer'):
        mode = 'assistant'
    list_endpoint = list_endpoint_for_mode(mode)

    if not validate_csrf():
        return redirect(url_for(list_endpoint, error='Invalid session token.'))

    name = (request.form.get('name') or '').strip()
    side = request.form.get('side', 'w').lower()
    history = request.form.get('history', '')
    trainer_elo = None
    if mode == 'trainer':
        try:
            trainer_elo = int(request.form.get('trainer_elo') or 1200)
        except ValueError:
            trainer_elo = 1200
        if trainer_elo not in TRAINER_ELO_LEVELS:
            trainer_elo = 1200
        # Trainer starts from empty board; ignore pasted history for clarity.
        history = ''

    if not name:
        return redirect(url_for(list_endpoint, error='Вкажіть назву партії.'))

    user_color = parse_color(side)
    try:
        board = parse_history(history)
    except ValueError as exc:
        return redirect(url_for(list_endpoint, error=f'Не вдалося розібрати історію: {exc}'))

    start_fen = starting_fen_from_board(board)
    history_text = format_move_history(board, start_fen)
    game_id = create_game(
        user_id=current_user_id(),
        name=name,
        history=history_text,
        user_color=color_code(user_color),
        start_fen=start_fen,
        mode=mode,
        trainer_elo=trainer_elo,
    )
    return redirect(url_for('play_game', game_id=game_id))


@app.route('/game/<int:game_id>')
@login_required
def play_game(game_id):
    game, board, start_fen = require_owned_game(game_id)
    if not game:
        return redirect(url_for('games_list', error='Партію не знайдено.'))

    opening_san = None
    board, opening_san = maybe_play_trainer_opening(game, board, start_fen)
    # Reload game row after possible opening move history update.
    game, board, start_fen = require_owned_game(game_id)

    if is_trainer_game(game):
        msg = (
            f'Trainer game "{game["name"]}" · you are '
            f'{side_label(parse_color(game["user_color"]))} · '
            f'{game.get("trainer_elo") or "?"} Elo'
        )
        if opening_san:
            msg += f'. Trainer opened with {opening_san}.'
        analyze = False
    else:
        msg = f'Playing "{game["name"]}" as {side_label(parse_color(game["user_color"]))}.'
        analyze = True

    payload = game_payload(game, board, start_fen, message=msg, analyze=analyze)
    payload['list_endpoint'] = list_endpoint_for_mode(game.get('mode') or 'assistant')
    return render_template('game.html', **payload)


@app.route('/games/<int:game_id>/delete', methods=['POST'])
@login_required
def remove_game(game_id):
    return_to = (request.form.get('return_to') or 'games').strip().lower()
    list_endpoint = 'training_list' if return_to == 'training' else 'games_list'
    if not validate_csrf():
        return redirect(url_for(list_endpoint, error='Invalid session token.'))
    delete_game(game_id, current_user_id())
    return redirect(url_for(list_endpoint))


@app.route('/api/state', methods=['GET'])
@login_required
def api_state():
    game_id = request.args.get('game_id', type=int)
    if not game_id:
        return jsonify({'ok': False, 'error': 'game_id required.'}), 400
    game, board, start_fen = require_owned_game(game_id)
    if not game:
        return jsonify({'ok': False, 'error': 'Game not found.'}), 404
    payload = game_payload(game, board, start_fen, analyze=False)
    payload['ok'] = True
    return jsonify(payload)


def apply_san_move(game, board, start_fen, san):
    san = (san or '').strip()
    if not san:
        return False, 'Enter a move in SAN, e.g. e4 or Nf6.', board, None
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, f'Invalid move: {san}', board, None

    played = board.san(parsed)
    board.push(parsed)
    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    return True, f'Played {played}.', board, parsed


def apply_trainer_user_move(game, board, start_fen, san):
    """Evaluate user move, optionally let Stockfish reply. Returns payload extras."""
    user_color = parse_color(game['user_color'])
    if board.turn != user_color:
        return False, 'Wait for the trainer to move.', board, None, None

    san = (san or '').strip()
    if not san:
        return False, 'Enter a move in SAN, e.g. e4 or Nf6.', board, None, None
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, f'Invalid move: {san}', board, None, None

    board_before = board.copy()
    engine = get_engine()
    with _engine_lock:
        coach = evaluate_user_move(engine, board_before, parsed, user_color)

    board.push(parsed)
    update_game_history(game['id'], current_user_id(), format_move_history(board, start_fen))

    engine_san = None
    if not board.is_game_over():
        elo = game.get('trainer_elo') or 1200
        with _engine_lock:
            engine_san = engine_reply(engine, board, elo)
        if engine_san:
            board.push_san(engine_san)
            update_game_history(
                game['id'], current_user_id(), format_move_history(board, start_fen)
            )

    message = coach['message']
    if engine_san:
        message += f' Trainer played {engine_san}.'
    return True, message, board, coach, engine_san


@app.route('/api/move', methods=['POST'])
@login_required
def play_move():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': 'Invalid CSRF token.'}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': 'game_id required.'}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': 'Game not found.'}), 404

    if is_trainer_game(game):
        ok, message, board, coach, _engine_san = apply_trainer_user_move(
            game, board, start_fen, payload.get('san', '')
        )
        if not ok:
            body = game_payload(game, board, start_fen, message='', error=message, analyze=False)
            body['ok'] = False
            return jsonify(body), 400
        body = game_payload(
            game, board, start_fen, message=message, analyze=False, coach=coach
        )
        body['ok'] = True
        return jsonify(body)

    ok, message, board, _parsed = apply_san_move(game, board, start_fen, payload.get('san', ''))
    if not ok:
        body = game_payload(game, board, start_fen, message='', error=message, analyze=False)
        body['ok'] = False
        return jsonify(body), 400

    body = game_payload(game, board, start_fen, message=message, analyze=True)
    body['ok'] = True
    return jsonify(body)


@app.route('/api/undo', methods=['POST'])
@login_required
def undo():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': 'Invalid CSRF token.'}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': 'game_id required.'}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': 'Game not found.'}), 404

    if not board.move_stack:
        body = game_payload(game, board, start_fen, error='No moves to undo.', analyze=False)
        body['ok'] = False
        return jsonify(body), 400

    # Trainer: undo engine reply + user move when possible.
    pops = 1
    if is_trainer_game(game):
        user_color = parse_color(game['user_color'])
        if board.turn == user_color and len(board.move_stack) >= 2:
            pops = 2
        elif board.turn != user_color and board.move_stack:
            pops = 1

    for _ in range(pops):
        if board.move_stack:
            board.pop()

    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    body = game_payload(
        game,
        board,
        start_fen,
        message='Undid last move.',
        analyze=not is_trainer_game(game),
        coach=None,
    )
    body['ok'] = True
    return jsonify(body)


@app.route('/api/hint', methods=['POST'])
@login_required
def hint():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': 'Invalid CSRF token.'}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': 'game_id required.'}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': 'Game not found.'}), 404

    user_color = parse_color(game['user_color'])
    if board.is_game_over():
        suggestions = []
        message = game_over_message(board)
    else:
        engine = get_engine()
        with _engine_lock:
            raw = analyze_position(engine, board, user_color)
        suggestions = [
            {
                **option,
                'first_move': option['moves'][0] if option['moves'] else '',
            }
            for option in raw
        ]
        message = 'Suggestions updated.' if suggestions else 'No suggestions for this position.'

    body = game_payload(
        game,
        board,
        start_fen,
        suggestions=suggestions,
        message=message,
        analyze=False,
    )
    body['ok'] = True
    return jsonify(body)


def main():
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '5050'))
    open_browser = os.environ.get('OPEN_BROWSER', '1') not in ('0', 'false', 'False')
    url = f'http://127.0.0.1:{port}'
    print(f'Chess move advisor UI: {url}')
    get_engine()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
