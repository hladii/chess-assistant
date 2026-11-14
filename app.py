import atexit
import os
import random
import secrets
import threading
import webbrowser
from datetime import datetime, timedelta

import chess
import chess.svg
from flask import (
    Flask,
    abort,
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
    board_at_ply,
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
    is_admin,
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
    apply_elo_result,
    create_game,
    create_puzzle_attempt,
    create_user,
    delete_game,
    get_accessible_game,
    get_active_duel_for_user,
    get_active_puzzle,
    get_puzzle_attempt_for_user,
    get_user_by_email,
    get_user_by_id,
    init_db,
    list_active_puzzles_for_user,
    list_duels_for_user,
    list_games,
    list_puzzle_attempt_history,
    list_users_for_admin,
    pop_quality_score,
    puzzle_stats_for_user,
    scores_from_game,
    search_users,
    update_game_history,
    update_puzzle_attempt,
    update_user_locale,
    update_user_password,
    update_user_profile,
)
from elo import result_from_board, result_from_resign
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
        'is_admin': is_admin(),
        'get_locale': get_locale,
        'current_locale': str(get_locale()),
        'supported_locales': SUPPORTED_LOCALES,
        'locale_labels': LOCALE_LABELS,
        'i18n_js': js_messages(),
    }


def require_admin():
    if not is_admin():
        abort(404)


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


def build_suggestions(board, user_color, *, ignore_turn=False):
    if board.is_game_over():
        return []
    if not ignore_turn and board.turn != user_color:
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


def analysis_board_for_game(game, board, start_fen, ply=None):
    """
    Board to run Stockfish on. For a terminal position with no ply given,
    step back one half-move so MultiPV has legal moves.
    """
    full_ply = len(board.move_stack)
    if ply is None:
        if board.is_game_over() and full_ply > 0:
            ply = full_ply - 1
        else:
            ply = full_ply
    else:
        try:
            ply = int(ply)
        except (TypeError, ValueError):
            ply = full_ply
        ply = max(0, min(ply, full_ply))

    analysis_board = board_at_ply(start_fen, game.get('history') or '', ply)
    return analysis_board, ply, full_ply


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


def is_duel_game(game):
    return (game.get('mode') or 'assistant') == 'duel'


def duel_user_color(game, user_id):
    if game.get('white_user_id') == user_id:
        return chess.WHITE
    if game.get('black_user_id') == user_id:
        return chess.BLACK
    return parse_color(game.get('user_color') or 'w')


def viewer_color(game, user_id=None):
    uid = user_id if user_id is not None else current_user_id()
    if is_duel_game(game):
        return duel_user_color(game, uid)
    return parse_color(game['user_color'])


def maybe_finalize_duel(game, board):
    """If a duel just ended on the board, persist result and apply Elo once."""
    if not is_duel_game(game):
        return None
    if game.get('elo_applied') and game.get('result'):
        return {
            'already_applied': True,
            'result': game.get('result'),
            'white_elo_before': game.get('white_elo_before'),
            'black_elo_before': game.get('black_elo_before'),
            'white_elo_after': game.get('white_elo_after'),
            'black_elo_after': game.get('black_elo_after'),
            'white_elo_delta': (
                (game.get('white_elo_after') or 0) - (game.get('white_elo_before') or 0)
                if game.get('white_elo_after') is not None
                else None
            ),
            'black_elo_delta': (
                (game.get('black_elo_after') or 0) - (game.get('black_elo_before') or 0)
                if game.get('black_elo_after') is not None
                else None
            ),
        }
    result = result_from_board(board)
    if not result:
        return None
    applied = apply_elo_result(game['id'], result)
    if applied:
        game.update(
            {
                'result': applied.get('result'),
                'elo_applied': True,
                'white_elo_before': applied.get('white_elo_before'),
                'black_elo_before': applied.get('black_elo_before'),
                'white_elo_after': applied.get('white_elo_after'),
                'black_elo_after': applied.get('black_elo_after'),
            }
        )
    return applied


def elo_payload_for_viewer(game, elo_info=None, user_id=None):
    uid = user_id if user_id is not None else current_user_id()
    if not is_duel_game(game):
        return {}
    info = elo_info or {}
    is_white = game.get('white_user_id') == uid
    before_key = 'white_elo_before' if is_white else 'black_elo_before'
    after_key = 'white_elo_after' if is_white else 'black_elo_after'
    delta_key = 'white_elo_delta' if is_white else 'black_elo_delta'
    your_before = info.get(before_key, game.get(before_key))
    your_after = info.get(after_key, game.get(after_key))
    your_delta = info.get(delta_key)
    if your_delta is None and your_before is not None and your_after is not None:
        your_delta = your_after - your_before
    opp_id = game.get('black_user_id') if is_white else game.get('white_user_id')
    opponent = get_user_by_id(opp_id) if opp_id else None
    return {
        'result': info.get('result') or game.get('result'),
        'elo_applied': bool(game.get('elo_applied') or bool(info.get('result'))),
        'your_elo': your_after if your_after is not None else (
            (get_user_by_id(uid) or {}).get('elo_rating')
        ),
        'your_elo_before': your_before,
        'your_elo_delta': your_delta,
        'opponent_name': (
            (opponent.get('display_name') or opponent.get('email') or '') if opponent else ''
        ),
        'opponent_elo': (
            info.get('black_elo_after' if is_white else 'white_elo_after')
            or game.get('black_elo_after' if is_white else 'white_elo_after')
            or (opponent.get('elo_rating') if opponent else None)
        ),
    }


def game_payload(game, board, start_fen, suggestions=None, message='', error='', analyze=False, coach=None, elo_info=None):
    user_id = current_user_id()
    user_color = viewer_color(game, user_id)
    duel = is_duel_game(game)
    ended_msg = game_over_message(board)
    resigned = duel and game.get('result') and not board.is_game_over()
    ended = bool(ended_msg) or bool(game.get('result')) or resigned
    if ended_msg:
        message = ended_msg
    elif resigned and game.get('result'):
        if game['result'] == '1-0':
            message = _('White wins by resignation.')
        elif game['result'] == '0-1':
            message = _('Black wins by resignation.')
        else:
            message = _('Draw.')
    your_turn = board.turn == user_color and not ended
    trainer = is_trainer_game(game)
    # In trainer mode the engine replies automatically — never ask for opponent SAN.
    if trainer and not your_turn and not ended:
        move_prompt = _('Trainer thinking…')
    elif duel and ended:
        move_prompt = _('Review position')
    elif duel and not your_turn and not ended:
        move_prompt = _('Waiting for opponent…')
    else:
        move_prompt = _('Your move') if your_turn else _('Opponent move')
    if suggestions is None:
        # Skip heavy multipv in trainer; duel only after game over.
        if trainer:
            suggestions = []
        elif duel:
            # Engine lines only on explicit Analyze (/api/hint), not every page render.
            suggestions = []
        else:
            suggestions = build_suggestions(board, user_color) if analyze and your_turn else []
    last_move = board.peek().uci() if board.move_stack else None
    orientation = 'white' if user_color == chess.WHITE else 'black'
    accuracy = accuracy_from_scores(scores_from_game(game))
    history_text = (game.get('history') or '').strip() or format_move_history(board, start_fen)
    full_move_count = len(board_from_game(game)[0].move_stack) if duel else len(board.move_stack)
    payload = {
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
        'move_count': full_move_count,
        'review_ply': len(board.move_stack),
        'accuracy': accuracy,
        'history': history_text,
        'csrf_token': get_csrf_token(),
        'can_resign': duel and not ended,
        'can_analyze': (not trainer and not duel) or (duel and ended),
        'can_undo': not duel and not ended,
    }
    if duel:
        payload.update(elo_payload_for_viewer(game, elo_info=elo_info, user_id=user_id))
    return payload


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
    """Return (game, board, start_fen) or abort 404 if not visible to current user."""
    user_id = current_user_id()
    game = get_accessible_game(game_id, user_id)
    if not game:
        abort(404)
    if is_duel_game(game):
        if user_id not in (game.get('white_user_id'), game.get('black_user_id')):
            abort(404)
    elif game.get('user_id') != user_id:
        abort(404)
    # Assistant games are admin-only (menu + /games).
    if not is_trainer_game(game) and not is_duel_game(game):
        require_admin()
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
                    user_id = create_user(
                        email, password, name, locale=DEFAULT_LOCALE
                    )
                    user = get_user_by_id(user_id)
                    login_user(user)
                    set_session_locale(DEFAULT_LOCALE)
                    resp = make_response(redirect(url_for('index')))
                    resp.set_cookie(
                        LOCALE_COOKIE,
                        DEFAULT_LOCALE,
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
        elif mode == 'duel':
            game['mode_label'] = _('Duel')
        else:
            game['mode_label'] = _('Assistant')
        scores = scores_from_game(game)
        game['accuracy'] = accuracy_from_scores(scores)
        game['quality_move_count'] = len(scores)
    return games


def annotate_duels(games, user_id):
    annotate_games(games)
    for game in games:
        is_white = game.get('white_user_id') == user_id
        game['side_label'] = _('White') if is_white else _('Black')
        opp_id = game.get('black_user_id') if is_white else game.get('white_user_id')
        opponent = get_user_by_id(opp_id) if opp_id else None
        game['opponent_name'] = (
            (opponent.get('display_name') or opponent.get('email') or _('Opponent'))
            if opponent
            else _('Opponent')
        )
        game['opponent_elo'] = opponent.get('elo_rating') if opponent else None
        if game.get('result') == '1-0':
            game['result_label'] = _('1–0')
            game['you_won'] = is_white
        elif game.get('result') == '0-1':
            game['result_label'] = _('0–1')
            game['you_won'] = not is_white
        elif game.get('result') == '1/2-1/2':
            game['result_label'] = _('½–½')
            game['you_won'] = None
        else:
            game['result_label'] = _('In progress')
            game['you_won'] = None
        before = game.get('white_elo_before' if is_white else 'black_elo_before')
        after = game.get('white_elo_after' if is_white else 'black_elo_after')
        game['your_elo_delta'] = (after - before) if before is not None and after is not None else None
        game['status_open'] = not bool(game.get('result'))
        game['your_turn'] = False
        if game['status_open']:
            try:
                board, _start = board_from_game(game)
                user_color = chess.WHITE if is_white else chess.BLACK
                game['your_turn'] = (
                    board.turn == user_color and not board.is_game_over()
                )
            except Exception:
                game['your_turn'] = False
    # Your-turn games first; keep SQL updated_at order within groups.
    games.sort(key=lambda g: 0 if g.get('your_turn') else 1)
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
    if mode == 'duel':
        return 'duels_list'
    return 'games_list'


@app.route('/')
@login_required
def index():
    return render_template('home.html')


@app.route('/users', methods=['GET'])
@login_required
def users_list():
    require_admin()
    users = list_users_for_admin()
    return render_template('users.html', users=users)


@app.route('/duels', methods=['GET'])
@login_required
def duels_list():
    user_id = current_user_id()
    user = get_user_by_id(user_id)
    games = list_duels_for_user(user_id)
    annotate_duels(games, user_id)
    return render_template(
        'duels.html',
        games=games,
        elo_rating=(user or {}).get('elo_rating'),
        error=request.args.get('error', ''),
    )


@app.route('/duels', methods=['POST'])
@login_required
def create_duel():
    if not validate_csrf():
        return redirect(url_for('duels_list', error=_('Invalid session token.')))

    name = (request.form.get('name') or '').strip()
    opponent_raw = (request.form.get('opponent_id') or '').strip()
    try:
        opponent_id = int(opponent_raw)
    except (TypeError, ValueError):
        opponent_id = None

    if not name:
        return redirect(url_for('duels_list', error=_('Enter a game name.')))
    if not opponent_id:
        return redirect(url_for('duels_list', error=_('Choose an opponent.')))

    me = current_user_id()
    if opponent_id == me:
        return redirect(url_for('duels_list', error=_('You cannot challenge yourself.')))

    opponent = get_user_by_id(opponent_id)
    if not opponent:
        return redirect(url_for('duels_list', error=_('Opponent not found.')))

    if random.choice((True, False)):
        white_id, black_id = me, opponent_id
        user_color = 'w'
    else:
        white_id, black_id = opponent_id, me
        user_color = 'b'

    start_fen = chess.STARTING_FEN
    game_id = create_game(
        user_id=me,
        name=name,
        history='',
        user_color=user_color,
        start_fen=start_fen,
        mode='duel',
        white_user_id=white_id,
        black_user_id=black_id,
    )
    return redirect(url_for('play_game', game_id=game_id))


@app.route('/api/users/search', methods=['GET'])
@login_required
def api_users_search():
    q = request.args.get('q', '')
    stripped = (q or '').strip()
    limit = 5 if not stripped else 10
    users = search_users(q, exclude_id=current_user_id(), limit=limit)
    return jsonify(
        {
            'ok': True,
            'query': stripped,
            'users': [
                {
                    'id': u['id'],
                    'display_name': u.get('display_name') or '',
                    'email': u.get('email') or '',
                    'elo_rating': u.get('elo_rating'),
                    'label': (u.get('display_name') or u.get('email') or str(u['id']))
                    + f" · {u.get('elo_rating') or 1200}",
                }
                for u in users
            ],
        }
    )


@app.route('/games', methods=['GET'])
@login_required
def games_list():
    require_admin()
    games = list_games(current_user_id(), modes=('assistant',))
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
    games = list_games(current_user_id(), modes=('trainer',))
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
    if mode == 'assistant':
        require_admin()
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

    opening_san = None
    board, opening_san = maybe_play_trainer_opening(game, board, start_fen)
    # Reload game row after possible opening move history update.
    game, board, start_fen = require_owned_game(game_id)

    elo_info = maybe_finalize_duel(game, board)
    user_color = viewer_color(game)

    if is_trainer_game(game):
        msg = _(
            'Trainer game "%(name)s" · you are %(side)s · %(elo)s Elo',
            name=game['name'],
            side=side_label(user_color),
            elo=game.get('trainer_elo') or '?',
        )
        if opening_san:
            msg += ' ' + _('Trainer opened with %(san)s.', san=opening_san)
        analyze = False
    elif is_duel_game(game):
        opp = elo_payload_for_viewer(game, elo_info=elo_info)
        msg = _(
            'Duel "%(name)s" · you are %(side)s vs %(opponent)s',
            name=game['name'],
            side=side_label(user_color),
            opponent=opp.get('opponent_name') or _('Opponent'),
        )
        if game.get('result') or board.is_game_over():
            msg += ' ' + _('Press Analyze to review engine lines. Click a move in the history to inspect that position.')
        analyze = False
    else:
        msg = _(
            'Playing "%(name)s" as %(side)s.',
            name=game['name'],
            side=side_label(user_color),
        )
        analyze = True

    payload = game_payload(
        game, board, start_fen, message=msg, analyze=analyze, elo_info=elo_info
    )
    payload['list_endpoint'] = list_endpoint_for_mode(game.get('mode') or 'assistant')
    return render_template('game.html', **payload)


@app.route('/games/<int:game_id>/delete', methods=['POST'])
@login_required
def remove_game(game_id):
    return_to = (request.form.get('return_to') or 'games').strip().lower()
    if return_to == 'training':
        list_endpoint = 'training_list'
    elif return_to == 'duels':
        list_endpoint = 'duels_list'
    else:
        require_admin()
        list_endpoint = 'games_list'
    if not validate_csrf():
        return redirect(url_for(list_endpoint, error=_('Invalid session token.')))
    if not delete_game(game_id, current_user_id()):
        if return_to == 'duels':
            return redirect(
                url_for(
                    list_endpoint,
                    error=_('Finish or resign the duel before deleting it.'),
                )
            )
        return redirect(url_for(list_endpoint, error=_('Could not delete this game.')))
    return redirect(url_for(list_endpoint))


@app.route('/games/<int:game_id>/resign', methods=['POST'])
@login_required
def resign_game_form(game_id):
    """Resign an open duel from the list page; Elo updates automatically."""
    if not validate_csrf():
        return redirect(url_for('duels_list', error=_('Invalid session token.')))

    user_id = current_user_id()
    game = get_active_duel_for_user(game_id, user_id)
    if not game:
        return redirect(url_for('duels_list', error=_('Not found.')))
    if game.get('result'):
        return redirect(url_for('duels_list', error=_('This duel is already finished.')))

    board, _start = board_from_game(game)
    if board.is_game_over():
        maybe_finalize_duel(game, board)
        return redirect(url_for('duels_list'))

    result = result_from_resign(viewer_color(game, user_id))
    apply_elo_result(game_id, result)
    return redirect(url_for('duels_list'))


@app.route('/api/state', methods=['GET'])
@login_required
def api_state():
    game_id = request.args.get('game_id', type=int)
    if not game_id:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400
    game, board, start_fen = require_owned_game(game_id)
    elo_info = maybe_finalize_duel(game, board)
    payload = game_payload(
        game, board, start_fen, analyze=False, elo_info=elo_info
    )
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


def apply_duel_move(game, board, start_fen, san):
    if game.get('result'):
        return False, _('This duel is already finished.'), board, None
    user_color = viewer_color(game)
    if board.turn != user_color:
        return False, _('Wait for your opponent to move.'), board, None

    san = (san or '').strip()
    if not san:
        return False, _('Enter a move in SAN, e.g. e4 or Nf6.'), board, None
    try:
        parsed = board.parse_san(san)
    except ValueError:
        return False, _('Invalid move: %(san)s', san=san), board, None

    played = board.san(parsed)
    board.push(parsed)
    update_game_history(game['id'], current_user_id(), format_move_history(board, start_fen))
    return True, _('Played %(san)s.', san=played), board, parsed


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

    if is_duel_game(game):
        ok, message, board, _parsed = apply_duel_move(
            game, board, start_fen, payload.get('san', '')
        )
        if not ok:
            body = game_payload(game, board, start_fen, message='', error=message, analyze=False)
            body['ok'] = False
            return jsonify(body), 400
        elo_info = maybe_finalize_duel(game, board)
        analyze = bool(game.get('result') or board.is_game_over())
        body = game_payload(
            game,
            board,
            start_fen,
            message=message,
            analyze=analyze,
            elo_info=elo_info,
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


@app.route('/api/resign', methods=['POST'])
@login_required
def resign_duel():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id')
    if game_id is None:
        return jsonify({'ok': False, 'error': _('game_id required.')}), 400

    game, board, start_fen = require_owned_game(int(game_id))
    if not is_duel_game(game):
        return jsonify({'ok': False, 'error': _('Only duels can be resigned.')}), 400
    if game.get('result') or board.is_game_over():
        elo_info = maybe_finalize_duel(game, board)
        body = game_payload(
            game, board, start_fen, message=_('This duel is already finished.'),
            analyze=True, elo_info=elo_info,
        )
        body['ok'] = False
        return jsonify(body), 400

    result = result_from_resign(viewer_color(game))
    elo_info = apply_elo_result(game['id'], result)
    if elo_info:
        game.update(
            {
                'result': elo_info.get('result'),
                'elo_applied': True,
                'white_elo_before': elo_info.get('white_elo_before'),
                'black_elo_before': elo_info.get('black_elo_before'),
                'white_elo_after': elo_info.get('white_elo_after'),
                'black_elo_after': elo_info.get('black_elo_after'),
            }
        )
    body = game_payload(
        game,
        board,
        start_fen,
        message=_('You resigned.'),
        analyze=True,
        elo_info=elo_info,
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

    if is_duel_game(game):
        body = game_payload(
            game, board, start_fen, error=_('Undo is not available in duels.'), analyze=False
        )
        body['ok'] = False
        return jsonify(body), 400

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
    for _ply in range(pops):
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

    if is_trainer_game(game):
        body = game_payload(
            game, board, start_fen, error=_('Hints are not available in trainer mode.'), analyze=False
        )
        body['ok'] = False
        return jsonify(body), 400

    if is_duel_game(game) and not (game.get('result') or board.is_game_over()):
        body = game_payload(
            game,
            board,
            start_fen,
            error=_('Analyze is available after the duel ends.'),
            analyze=False,
        )
        body['ok'] = False
        return jsonify(body), 400

    user_color = viewer_color(game)
    analysis_board, review_ply, full_ply = analysis_board_for_game(
        game, board, start_fen, ply=payload.get('ply')
    )
    engine = get_engine()
    with _engine_lock:
        raw = analyze_position(engine, analysis_board, user_color)
    suggestions = [
        {
            **option,
            'first_move': option['moves'][0] if option['moves'] else '',
        }
        for option in raw
    ]

    if is_duel_game(game) or board.is_game_over() or game.get('result'):
        if suggestions:
            if review_ply < full_ply:
                message = _(
                    'Analysis after %(ply)s half-moves (before the final position).',
                    ply=review_ply,
                )
            else:
                message = _('Suggestions updated.')
        else:
            message = game_over_message(board) or _('No suggestions for this position.')
    else:
        message = _('Suggestions updated.') if suggestions else _('No suggestions for this position.')

    # Show the reviewed position on the board; keep game.result so UI stays in analyze mode.
    body = game_payload(
        game,
        analysis_board,
        start_fen,
        suggestions=suggestions,
        message=message.strip(),
        analyze=False,
    )
    body['review_ply'] = review_ply
    body['ok'] = True
    return jsonify(body)


def _wants_json_error():
    if request.path.startswith('/api/'):
        return True
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    return best == 'application/json' and (
        request.accept_mimetypes[best] > request.accept_mimetypes['text/html']
    )


def _error_message(code):
    messages = {
        401: _('Authentication required.'),
        403: _('You do not have permission to do that.'),
        404: _('Not found.'),
        500: _('Something went wrong on our side.'),
    }
    return messages.get(code, _('Something went wrong.'))


def _render_http_error(code):
    if _wants_json_error():
        return jsonify({'ok': False, 'error': _error_message(code)}), code
    template = f'errors/{code}.html'
    return render_template(template), code


@app.errorhandler(401)
def error_401(exc):
    return _render_http_error(401)


@app.errorhandler(403)
def error_403(exc):
    return _render_http_error(403)


@app.errorhandler(404)
def error_404(exc):
    return _render_http_error(404)


@app.errorhandler(500)
def error_500(exc):
    return _render_http_error(500)


def _puzzle_goal_label(goal):
    if goal == 'win_material':
        return _('Win material')
    return _('Checkmate')


def _puzzle_status_label(status):
    if status == 'solved':
        return _('Solved')
    if status == 'failed':
        return _('Failed')
    if status == 'abandoned':
        return _('Abandoned')
    return _('Not tried')


def _board_after_puzzle_plies(puzzle, ply_index):
    board = chess.Board(puzzle['fen'])
    solution = puzzle.get('solution_sans') or []
    applied = []
    for i in range(min(ply_index, len(solution))):
        move = board.parse_san(solution[i])
        san = board.san(move)
        board.push(move)
        applied.append(san)
    return board, applied


def _puzzle_last_move_uci(board):
    if not board.move_stack:
        return ''
    move = board.peek()
    return move.uci()


def _puzzle_payload(puzzle, attempt, board, message='', error='', status=None):
    solution = puzzle.get('solution_sans') or []
    ply = int(attempt.get('ply_index') or 0)
    finished = (status or attempt.get('status')) in ('solved', 'failed', 'abandoned')
    orientation = 'white' if puzzle.get('side_to_move') == 'w' else 'black'
    user_side = _('White') if puzzle.get('side_to_move') == 'w' else _('Black')
    return {
        'ok': not error,
        'puzzle_id': puzzle['id'],
        'attempt_id': attempt['id'],
        'slug': puzzle.get('slug'),
        'title': puzzle.get('title'),
        'goal': puzzle.get('goal'),
        'goal_label': _puzzle_goal_label(puzzle.get('goal')),
        'difficulty': puzzle.get('difficulty'),
        'themes': puzzle.get('themes') or [],
        'fen': board.fen(),
        'orientation': orientation,
        'user_side': user_side,
        'side_to_move': _('White') if board.turn == chess.WHITE else _('Black'),
        'your_turn': (not finished) and (
            (board.turn == chess.WHITE and puzzle.get('side_to_move') == 'w')
            or (board.turn == chess.BLACK and puzzle.get('side_to_move') == 'b')
        ),
        'ply_index': ply,
        'solution_length': len(solution),
        'moves_played': attempt.get('moves_played') or [],
        'last_move': _puzzle_last_move_uci(board),
        'status': status or attempt.get('status') or 'in_progress',
        'status_label': _puzzle_status_label(status or attempt.get('status')),
        'solved': (status or attempt.get('status')) == 'solved',
        'failed': (status or attempt.get('status')) == 'failed',
        'finished': finished,
        'message': message,
        'error': error,
        'history': ' '.join(attempt.get('moves_played') or []),
    }


def annotate_puzzles_for_list(puzzles):
    for puzzle in puzzles:
        status = puzzle.get('user_status')
        puzzle['status_label'] = _puzzle_status_label(status)
        puzzle['goal_label'] = _puzzle_goal_label(puzzle.get('goal'))
        puzzle['solved'] = status == 'solved'
        puzzle['failed'] = status == 'failed'
        puzzle['player_moves'] = (len(puzzle.get('solution_sans') or []) + 1) // 2
    return puzzles


@app.route('/puzzles', methods=['GET'])
@login_required
def puzzles_list():
    user_id = current_user_id()
    puzzles = annotate_puzzles_for_list(list_active_puzzles_for_user(user_id))
    stats = puzzle_stats_for_user(user_id)
    history = list_puzzle_attempt_history(user_id, limit=20)
    for item in history:
        item['status_label'] = _puzzle_status_label(item.get('status'))
        item['goal_label'] = _puzzle_goal_label(item.get('goal'))
        item['when_label'] = format_updated_at(item.get('finished_at') or item.get('created_at'))
    stats['last_activity_label'] = format_updated_at(stats.get('last_activity'))
    return render_template(
        'puzzles.html',
        puzzles=puzzles,
        stats=stats,
        history=history,
    )


@app.route('/puzzles/<int:puzzle_id>', methods=['GET'])
@login_required
def play_puzzle(puzzle_id):
    puzzle = get_active_puzzle(puzzle_id)
    if not puzzle:
        abort(404)
    attempt = create_puzzle_attempt(current_user_id(), puzzle_id)
    board = chess.Board(puzzle['fen'])
    payload = _puzzle_payload(
        puzzle,
        attempt,
        board,
        message=_('Find the winning line.'),
    )
    payload['csrf_token'] = get_csrf_token()
    return render_template('puzzle.html', **payload)


@app.route('/api/puzzle/move', methods=['POST'])
@login_required
def api_puzzle_move():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    attempt_id = payload.get('attempt_id')
    san = (payload.get('san') or '').strip()
    if attempt_id is None:
        return jsonify({'ok': False, 'error': _('attempt_id required.')}), 400
    if not san:
        return jsonify({'ok': False, 'error': _('Enter a move in SAN, e.g. e4 or Nf6.')}), 400

    user_id = current_user_id()
    attempt = get_puzzle_attempt_for_user(int(attempt_id), user_id)
    if not attempt:
        return jsonify({'ok': False, 'error': _('Not found.')}), 404
    if attempt.get('status') != 'in_progress':
        puzzle = get_active_puzzle(attempt['puzzle_id'])
        board, _ = _board_after_puzzle_plies(puzzle, attempt.get('ply_index') or 0)
        body = _puzzle_payload(
            puzzle, attempt, board, error=_('This attempt is already finished.')
        )
        body['ok'] = False
        return jsonify(body), 400

    puzzle = get_active_puzzle(attempt['puzzle_id'])
    if not puzzle:
        return jsonify({'ok': False, 'error': _('Not found.')}), 404

    solution = puzzle.get('solution_sans') or []
    ply = int(attempt.get('ply_index') or 0)
    board, applied = _board_after_puzzle_plies(puzzle, ply)
    moves_played = list(attempt.get('moves_played') or [])

    if ply >= len(solution):
        update_puzzle_attempt(
            attempt['id'], user_id, status='solved', finished=True, ply_index=ply,
            moves_played=moves_played,
        )
        attempt['status'] = 'solved'
        body = _puzzle_payload(puzzle, attempt, board, message=_('Puzzle solved!'), status='solved')
        return jsonify(body)

    expected_san = solution[ply]
    try:
        user_move = board.parse_san(san)
        expected_move = board.parse_san(expected_san)
    except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        body = _puzzle_payload(
            puzzle, attempt, board, error=_('Illegal move.'), status='in_progress'
        )
        body['ok'] = False
        return jsonify(body), 400

    if user_move != expected_move:
        moves_played.append(board.san(user_move))
        update_puzzle_attempt(
            attempt['id'],
            user_id,
            status='failed',
            moves_played=moves_played,
            ply_index=ply,
            finished=True,
        )
        attempt['moves_played'] = moves_played
        attempt['status'] = 'failed'
        # Show the wrong move on the board briefly in response fen
        board.push(user_move)
        body = _puzzle_payload(
            puzzle,
            attempt,
            board,
            error=_('Wrong move. Try again.'),
            status='failed',
        )
        body['ok'] = False
        return jsonify(body), 400

    played_san = board.san(user_move)
    board.push(user_move)
    moves_played.append(played_san)
    ply += 1
    auto_sans = []

    if ply < len(solution):
        reply_san = solution[ply]
        reply_move = board.parse_san(reply_san)
        auto_san = board.san(reply_move)
        board.push(reply_move)
        moves_played.append(auto_san)
        auto_sans.append(auto_san)
        ply += 1

    if ply >= len(solution):
        update_puzzle_attempt(
            attempt['id'],
            user_id,
            status='solved',
            moves_played=moves_played,
            ply_index=ply,
            finished=True,
        )
        attempt['moves_played'] = moves_played
        attempt['ply_index'] = ply
        attempt['status'] = 'solved'
        body = _puzzle_payload(
            puzzle, attempt, board, message=_('Puzzle solved!'), status='solved'
        )
        body['auto_moves'] = auto_sans
        return jsonify(body)

    update_puzzle_attempt(
        attempt['id'],
        user_id,
        status='in_progress',
        moves_played=moves_played,
        ply_index=ply,
    )
    attempt['moves_played'] = moves_played
    attempt['ply_index'] = ply
    body = _puzzle_payload(
        puzzle,
        attempt,
        board,
        message=_('Good. Keep going.'),
        status='in_progress',
    )
    body['auto_moves'] = auto_sans
    return jsonify(body)


@app.route('/api/puzzle/retry', methods=['POST'])
@login_required
def api_puzzle_retry():
    if not validate_csrf():
        return jsonify({'ok': False, 'error': _('Invalid CSRF token.')}), 403

    payload = request.get_json(silent=True) or {}
    puzzle_id = payload.get('puzzle_id')
    if puzzle_id is None:
        return jsonify({'ok': False, 'error': _('puzzle_id required.')}), 400

    puzzle = get_active_puzzle(int(puzzle_id))
    if not puzzle:
        return jsonify({'ok': False, 'error': _('Not found.')}), 404

    prev_id = payload.get('attempt_id')
    if prev_id is not None:
        prev = get_puzzle_attempt_for_user(int(prev_id), current_user_id())
        if prev and prev.get('status') == 'in_progress':
            update_puzzle_attempt(
                prev['id'],
                current_user_id(),
                status='abandoned',
                finished=True,
            )

    attempt = create_puzzle_attempt(current_user_id(), puzzle['id'])
    board = chess.Board(puzzle['fen'])
    body = _puzzle_payload(
        puzzle, attempt, board, message=_('Find the winning line.'), status='in_progress'
    )
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
