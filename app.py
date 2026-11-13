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
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import Babel, get_locale
from flask_babel import gettext as _

from assistant import (
    TRAINER_ELO_LEVELS,
    analyze_position,
    board_from_saved_history,
    classification_to_points,
    engine_reply,
    evaluate_user_move,
    find_engine,
    game_over_message,
    parse_history,
)
from auth import (
    current_user_email,
    current_user_id,
    current_user_initials,
    current_user_name,
    get_csrf_token,
    is_authenticated,
    login_required,
    login_user,
    logout_user,
    user_initials,
    validate_csrf,
    validate_display_name,
    validate_email,
    validate_new_password,
    verify_password,
)
from db import (
    accuracy_from_scores,
    append_quality_score,
    create_game,
    create_user,
    delete_game,
    get_game_for_user,
    get_user_by_email,
    get_user_by_id,
    init_db,
    list_games,
    pop_quality_score,
    scores_from_game,
    update_game_history,
    update_user_locale,
    update_user_password,
    update_user_profile,
)
from i18n import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE,
    LOCALE_COOKIE_MAX_AGE,
    LOCALE_LABELS,
    SUPPORTED_LOCALES,
    js_messages,
    normalize_locale,
    resolve_locale,
    set_session_locale,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') in ('1', 'true', 'True'),
    BABEL_DEFAULT_LOCALE=DEFAULT_LOCALE,
    BABEL_TRANSLATION_DIRECTORIES='translations',
)

babel = Babel()


def select_locale():
    user = None
    uid = current_user_id()
    if uid:
        user = get_user_by_id(uid)
    return resolve_locale(user)


babel.init_app(app, locale_selector=select_locale)

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
        'current_user_name': current_user_name(),
        'current_user_initials': current_user_initials(),
        'is_authenticated': is_authenticated(),
        'get_locale': get_locale,
        'current_locale': str(get_locale()),
        'supported_locales': SUPPORTED_LOCALES,
        'locale_labels': LOCALE_LABELS,
        'i18n_js': js_messages(),
    }


def side_label(color):
    return _('White') if color == chess.WHITE else _('Black')


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
        return _('No moves yet')
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
        move_prompt = _('Trainer thinking…')
    else:
        move_prompt = _('Your move') if your_turn else _('Opponent move')
    if suggestions is None:
        # Skip heavy multipv suggestions in trainer to save CPU on small VMs.
        if trainer:
            suggestions = []
        else:
            suggestions = build_suggestions(board, user_color) if analyze and your_turn else []
    last_move = board.peek().uci() if board.move_stack else None
    orientation = 'white' if user_color == chess.WHITE else 'black'
    accuracy = accuracy_from_scores(scores_from_game(game))
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
        'fen': board.fen(),
        'orientation': orientation,
        'last_move': last_move,
        'game_over': bool(ended),
        'move_count': len(board.move_stack),
        'accuracy': accuracy,
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
            error = _('Invalid session token. Refresh the page.')
        else:
            email, email_error = validate_email(request.form.get('email', ''))
            password = request.form.get('password') or ''
            if email_error:
                error = email_error
            else:
                user = get_user_by_email(email)
                if not user or not verify_password(user['password_hash'], password):
                    error = _('Invalid email or password.')
                else:
                    login_user(user)
                    next_url = request.args.get('next') or url_for('index')
                    if not next_url.startswith('/'):
                        next_url = url_for('index')
                    resp = make_response(redirect(next_url))
                    resp.set_cookie(
                        LOCALE_COOKIE,
                        session.get('locale') or DEFAULT_LOCALE,
                        max_age=LOCALE_COOKIE_MAX_AGE,
                        samesite='Lax',
                    )
                    return resp

    return render_template('login.html', error=error, csrf_token=get_csrf_token())


@app.route('/register', methods=['GET', 'POST'])
def register():
    if is_authenticated():
        return redirect(url_for('index'))

    error = ''
    if request.method == 'POST':
        if not validate_csrf():
            error = _('Invalid session token. Refresh the page.')
        else:
            name, name_error = validate_display_name(request.form.get('display_name', ''))
            email, email_error = validate_email(request.form.get('email', ''))
            password = request.form.get('password') or ''
            confirm = request.form.get('confirm_password') or ''
            if name_error:
                error = name_error
            elif email_error:
                error = email_error
            else:
                pwd_error = validate_new_password(password, confirm)
                if pwd_error:
                    error = pwd_error
                elif get_user_by_email(email):
                    error = _('A user with this email already exists.')
                else:
                    user_id = create_user(email, password, name)
                    user = get_user_by_id(user_id)
                    login_user(user)
                    resp = make_response(redirect(url_for('index')))
                    resp.set_cookie(
                        LOCALE_COOKIE,
                        session.get('locale') or DEFAULT_LOCALE,
                        max_age=LOCALE_COOKIE_MAX_AGE,
                        samesite='Lax',
                    )
                    return resp

    return render_template('register.html', error=error, csrf_token=get_csrf_token())


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    if not validate_csrf():
        flash(_('Invalid session token.'), 'error')
        return redirect(url_for('index'))
    logout_user()
    return redirect(url_for('login'))


@app.route('/set-locale', methods=['POST'])
def set_locale():
    """Guest (or anyone) language preference via cookie + session."""
    next_url = (request.form.get('next') or '').strip() or url_for('login')
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('login')
    # Avoid awkward trailing "?" from request.full_path when no query string.
    if next_url.endswith('?'):
        next_url = next_url[:-1]

    if not validate_csrf():
        return redirect(next_url)

    loc = set_session_locale(request.form.get('locale', ''))
    # Persist for logged-in users too when they use the guest bar (rare).
    uid = current_user_id()
    if uid:
        update_user_locale(uid, loc)

    resp = make_response(redirect(next_url))
    resp.set_cookie(
        LOCALE_COOKIE,
        loc,
        max_age=LOCALE_COOKIE_MAX_AGE,
        samesite='Lax',
    )
    return resp


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_user_by_id(current_user_id())
    message = ''
    error = ''
    form = (request.form.get('form') or '').strip()
    locale_cookie = None

    if request.method == 'POST':
        if not validate_csrf():
            error = _('Invalid session token. Refresh the page.')
        elif form == 'profile':
            name, name_error = validate_display_name(request.form.get('display_name', ''))
            if name_error:
                error = name_error
            else:
                update_user_profile(user['id'], name)
                user = get_user_by_id(user['id'])
                login_user(user)  # refresh session name
                message = _('Profile updated.')
        elif form == 'password':
            current_password = request.form.get('current_password') or ''
            new_password = request.form.get('new_password') or ''
            confirm_password = request.form.get('confirm_password') or ''
            if not verify_password(user['password_hash'], current_password):
                error = _('Current password is incorrect.')
            else:
                pwd_error = validate_new_password(new_password, confirm_password)
                if pwd_error:
                    error = pwd_error
                elif verify_password(user['password_hash'], new_password):
                    error = _('New password must be different from the current one.')
                else:
                    update_user_password(user['id'], new_password)
                    message = _('Password updated.')
                    user = get_user_by_id(user['id'])
        elif form == 'locale':
            loc = normalize_locale(request.form.get('locale', '')) or DEFAULT_LOCALE
            update_user_locale(user['id'], loc)
            set_session_locale(loc)
            locale_cookie = loc
            user = get_user_by_id(user['id'])
            message = _('Language updated.')
        else:
            error = _('Unknown action.')

    html = render_template(
        'profile.html',
        user=user,
        user_initials=user_initials(user.get('display_name'), user.get('email')),
        message=message,
        error=error,
        csrf_token=get_csrf_token(),
    )
    resp = make_response(html)
    if locale_cookie:
        resp.set_cookie(
            LOCALE_COOKIE,
            locale_cookie,
            max_age=LOCALE_COOKIE_MAX_AGE,
            samesite='Lax',
        )
    return resp


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
            game['mode_label'] = _('Trainer · %(elo)s Elo', elo=game.get('trainer_elo') or '?')
        else:
            game['mode_label'] = _('Assistant')
        scores = scores_from_game(game)
        game['accuracy'] = accuracy_from_scores(scores)
        game['quality_move_count'] = len(scores)
    return games


def summarize_accuracy(games):
    total_points = 0
    total_moves = 0
    for game in games:
        scores = scores_from_game(game)
        if not scores:
            continue
        total_points += sum(scores)
        total_moves += len(scores)
    if not total_moves:
        return None
    return int(round(total_points / total_moves))


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
        overall_accuracy=summarize_accuracy(games),
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
        overall_accuracy=summarize_accuracy(games),
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
        return redirect(url_for(list_endpoint, error=_('Invalid session token.')))

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
        return redirect(url_for(list_endpoint, error=_('Enter a game name.')))

    user_color = parse_color(side)
    try:
        board = parse_history(history)
    except ValueError as exc:
        return redirect(url_for(list_endpoint, error=_('Could not parse history: %(exc)s', exc=exc)))

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
        return redirect(url_for('games_list', error=_('Game not found.')))

    opening_san = None
    board, opening_san = maybe_play_trainer_opening(game, board, start_fen)
    # Reload game row after possible opening move history update.
    game, board, start_fen = require_owned_game(game_id)

    if is_trainer_game(game):
        msg = _(
            'Trainer game "%(name)s" · you are %(side)s · %(elo)s Elo',
            name=game['name'],
            side=side_label(parse_color(game['user_color'])),
            elo=game.get('trainer_elo') or '?',
        )
        if opening_san:
            msg += ' ' + _('Trainer opened with %(san)s.', san=opening_san)
        analyze = False
    else:
        msg = _(
            'Playing "%(name)s" as %(side)s.',
            name=game['name'],
            side=side_label(parse_color(game['user_color'])),
        )
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
        return redirect(url_for(list_endpoint, error=_('Invalid session token.')))
    delete_game(game_id, current_user_id())
    return redirect(url_for(list_endpoint))


@app.route('/api/state', methods=['GET'])
@login_required
def api_state():
    game_id = request.args.get('game_id', type=int)
    if not game_id:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400
    game, board, start_fen = require_owned_game(game_id)
    if not game:
        return jsonify({'ok': False, 'error': _('Game not found.')}), 404
    payload = game_payload(game, board, start_fen, analyze=False)
    payload['ok'] = True
    return jsonify(payload)


def apply_san_move(game, board, start_fen, san):
    san = (san or '').strip()
    if not san:
        return False, _('Enter a move in SAN, e.g. e4 or Nf6.'), board, None, None
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, _('Invalid move: %(san)s', san=san), board, None, None

    user_color = parse_color(game['user_color'])
    scoring_user_move = board.turn == user_color
    coach = None
    if scoring_user_move:
        board_before = board.copy()
        engine = get_engine()
        with _engine_lock:
            coach = evaluate_user_move(engine, board_before, parsed, user_color)
        points = classification_to_points(coach['classification'])
        append_quality_score(game['id'], current_user_id(), points)
        scores = scores_from_game(game)
        scores.append(points)
        game['quality_scores'] = scores

    played = board.san(parsed)
    board.push(parsed)
    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    message = coach['message'] if coach else _('Played %(san)s.', san=played)
    return True, message, board, parsed, coach


def apply_trainer_user_move(game, board, start_fen, san):
    """Evaluate user move, optionally let Stockfish reply. Returns payload extras."""
    user_color = parse_color(game['user_color'])
    if board.turn != user_color:
        return False, _('Wait for the trainer to move.'), board, None, None

    san = (san or '').strip()
    if not san:
        return False, _('Enter a move in SAN, e.g. e4 or Nf6.'), board, None, None
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, _('Invalid move: %(san)s', san=san), board, None, None

    board_before = board.copy()
    engine = get_engine()
    with _engine_lock:
        coach = evaluate_user_move(engine, board_before, parsed, user_color)

    points = classification_to_points(coach['classification'])
    append_quality_score(game['id'], current_user_id(), points)
    scores = scores_from_game(game)
    scores.append(points)
    game['quality_scores'] = scores

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
        message += ' ' + _('Trainer played %(san)s.', san=engine_san)
    return True, message, board, coach, engine_san


@app.route('/api/move', methods=['POST'])
@login_required
def play_move():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': _('Game not found.')}), 404

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

    ok, message, board, _parsed, coach = apply_san_move(
        game, board, start_fen, payload.get('san', '')
    )
    if not ok:
        body = game_payload(game, board, start_fen, message='', error=message, analyze=False)
        body['ok'] = False
        return jsonify(body), 400

    body = game_payload(
        game, board, start_fen, message=message, analyze=True, coach=coach
    )
    body['ok'] = True
    return jsonify(body)


@app.route('/api/undo', methods=['POST'])
@login_required
def undo():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': _('Game not found.')}), 404

    if not board.move_stack:
        body = game_payload(game, board, start_fen, error=_('No moves to undo.'), analyze=False)
        body['ok'] = False
        return jsonify(body), 400

    # Trainer: undo engine reply + user move when possible.
    user_color = parse_color(game['user_color'])
    pops = 1
    if is_trainer_game(game):
        if board.turn == user_color and len(board.move_stack) >= 2:
            pops = 2
        elif board.turn != user_color and board.move_stack:
            pops = 1

    removed_user_move = False
    for _ in range(pops):
        if not board.move_stack:
            break
        # Color that just played the ply we are about to pop.
        mover = not board.turn
        if mover == user_color:
            removed_user_move = True
        board.pop()

    if removed_user_move:
        pop_quality_score(game['id'], current_user_id())
        scores = scores_from_game(game)
        if scores:
            scores.pop()
        game['quality_scores'] = scores

    history = format_move_history(board, start_fen)
    update_game_history(game['id'], current_user_id(), history)
    body = game_payload(
        game,
        board,
        start_fen,
        message=_('Undid last move.'),
        analyze=not is_trainer_game(game),
        coach=None,
    )
    body['ok'] = True
    return jsonify(body)


@app.route('/api/hint', methods=['POST'])
@login_required
def hint():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not game:
        return jsonify({'ok': False, 'error': _('Game not found.')}), 404

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
        message = _('Suggestions updated.') if suggestions else _('No suggestions for this position.')

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
