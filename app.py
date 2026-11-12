import atexit
import os
import secrets
import threading
import webbrowser
from datetime import timedelta

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
    analyze_position,
    board_from_saved_history,
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


def game_payload(game, board, start_fen, suggestions=None, message='', error='', analyze=False):
    user_color = parse_color(game['user_color'])
    ended = game_over_message(board)
    if ended:
        message = ended
    your_turn = board.turn == user_color and not ended
    if suggestions is None:
        suggestions = build_suggestions(board, user_color) if analyze and your_turn else []
    return {
        'started': True,
        'game_id': game['id'],
        'name': game['name'],
        'user_side': side_label(user_color),
        'side_to_move': side_label(board.turn),
        'your_turn': your_turn,
        'move_prompt': 'Your move' if your_turn else 'Opponent move',
        'suggestions': suggestions,
        'message': message,
        'error': error,
        'board_svg': board_svg(board, user_color),
        'game_over': bool(ended),
        'move_count': len(board.move_stack),
        'history': format_move_history(board, start_fen),
        'csrf_token': get_csrf_token(),
    }


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


@app.route('/')
@login_required
def index():
    games = list_games(current_user_id())
    for game in games:
        game['side_label'] = side_label(parse_color(game['user_color']))
        game['history_preview'] = history_preview(game['history'])
    return render_template(
        'index.html',
        games=games,
        error=request.args.get('error', ''),
        csrf_token=get_csrf_token(),
    )


@app.route('/games', methods=['POST'])
@login_required
def create():
    if not validate_csrf():
        return redirect(url_for('index', error='Invalid session token.'))

    name = (request.form.get('name') or '').strip()
    side = request.form.get('side', 'w').lower()
    history = request.form.get('history', '')
    if not name:
        return redirect(url_for('index', error='Enter a game name.'))

    user_color = parse_color(side)
    try:
        board = parse_history(history)
    except ValueError as exc:
        return redirect(url_for('index', error=f'Could not parse history: {exc}'))

    start_fen = starting_fen_from_board(board)
    history_text = format_move_history(board, start_fen)
    game_id = create_game(
        user_id=current_user_id(),
        name=name,
        history=history_text,
        user_color=color_code(user_color),
        start_fen=start_fen,
    )
    return redirect(url_for('play_game', game_id=game_id))


@app.route('/game/<int:game_id>')
@login_required
def play_game(game_id):
    game, board, start_fen = require_owned_game(game_id)
    if not game:
        return redirect(url_for('index', error='Game not found.'))
    payload = game_payload(
        game,
        board,
        start_fen,
        message=f'Playing "{game["name"]}" as {side_label(parse_color(game["user_color"]))}.',
        analyze=True,
    )
    return render_template('game.html', **payload)


@app.route('/games/<int:game_id>/delete', methods=['POST'])
@login_required
def remove_game(game_id):
    if not validate_csrf():
        return redirect(url_for('index', error='Invalid session token.'))
    delete_game(game_id, current_user_id())
    return redirect(url_for('index'))


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
        return False, 'Enter a move in SAN, e.g. e4 or Nf6.', board
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, f'Invalid move: {san}', board

    played = board.san(parsed)
    board.push(parsed)
    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    return True, f'Played {played}.', board


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

    ok, message, board = apply_san_move(game, board, start_fen, payload.get('san', ''))
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

    board.pop()
    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    body = game_payload(game, board, start_fen, message='Undid last move.', analyze=True)
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
