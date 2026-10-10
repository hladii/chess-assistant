import atexit
import threading
import webbrowser
from dataclasses import dataclass, field

import chess
import chess.svg
from flask import Flask, jsonify, redirect, render_template, request, url_for

from assistant import (
    analyze_position,
    board_from_saved_history,
    find_engine,
    game_over_message,
    parse_history,
)
from db import (
    create_game,
    delete_game,
    get_game,
    init_db,
    list_games,
    update_game_history,
)

app = Flask(__name__)
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


@dataclass
class GameState:
    game_id: int = None
    name: str = ''
    board: chess.Board = field(default_factory=chess.Board)
    start_fen: str = chess.STARTING_FEN
    user_color: chess.Color = chess.WHITE
    started: bool = False
    suggestions: list = field(default_factory=list)
    message: str = ''
    error: str = ''


state = GameState()


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


def persist_history():
    if not state.started or state.game_id is None:
        return
    history = format_move_history(state.board, state.start_fen)
    update_game_history(state.game_id, history)


def refresh_suggestions():
    state.suggestions = []
    if not state.started or state.board.is_game_over():
        return
    if state.board.turn != state.user_color:
        return
    engine = get_engine()
    with _engine_lock:
        raw = analyze_position(engine, state.board, state.user_color)
    state.suggestions = [
        {
            **option,
            'first_move': option['moves'][0] if option['moves'] else '',
        }
        for option in raw
    ]


def board_svg():
    lastmove = state.board.peek() if state.board.move_stack else None
    check = state.board.king(state.board.turn) if state.board.is_check() else None
    return chess.svg.board(
        board=state.board,
        orientation=state.user_color,
        lastmove=lastmove,
        check=check,
        size=420,
    )


def load_game_into_state(game_row, analyze=True):
    start_fen = game_row.get('start_fen') or chess.STARTING_FEN
    board = board_from_saved_history(start_fen, game_row.get('history') or '')
    state.game_id = game_row['id']
    state.name = game_row['name']
    state.user_color = parse_color(game_row['user_color'])
    state.start_fen = start_fen
    state.board = board
    state.started = True
    state.suggestions = []
    state.error = ''
    state.message = f'Playing "{state.name}" as {side_label(state.user_color)}.'
    if analyze:
        refresh_suggestions()


def clear_state():
    state.game_id = None
    state.name = ''
    state.board = chess.Board()
    state.start_fen = chess.STARTING_FEN
    state.user_color = chess.WHITE
    state.started = False
    state.suggestions = []
    state.message = ''
    state.error = ''


def game_payload():
    ended = game_over_message(state.board) if state.started else None
    if ended:
        state.message = ended
    your_turn = state.started and state.board.turn == state.user_color and not ended
    return {
        'started': state.started,
        'game_id': state.game_id,
        'name': state.name,
        'user_side': side_label(state.user_color) if state.started else '',
        'side_to_move': side_label(state.board.turn),
        'your_turn': your_turn,
        'move_prompt': 'Your move' if your_turn else 'Opponent move',
        'suggestions': state.suggestions,
        'message': state.message,
        'error': state.error,
        'board_svg': board_svg() if state.started else '',
        'game_over': bool(ended),
        'move_count': len(state.board.move_stack),
        'history': format_move_history(state.board, state.start_fen) if state.started else '',
    }


def history_preview(history, limit=72):
    text = (history or '').strip()
    if not text:
        return 'No moves yet'
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


@app.route('/')
def index():
    clear_state()
    games = list_games()
    for game in games:
        game['side_label'] = side_label(parse_color(game['user_color']))
        game['history_preview'] = history_preview(game['history'])
    return render_template(
        'index.html',
        games=games,
        error=request.args.get('error', ''),
    )


@app.route('/games', methods=['POST'])
def create():
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
        name=name,
        history=history_text,
        user_color=color_code(user_color),
        start_fen=start_fen,
    )
    return redirect(url_for('play_game', game_id=game_id))


@app.route('/game/<int:game_id>')
def play_game(game_id):
    game = get_game(game_id)
    if not game:
        return redirect(url_for('index', error='Game not found.'))
    load_game_into_state(game, analyze=True)
    return render_template('game.html', **game_payload())


@app.route('/games/<int:game_id>/delete', methods=['POST'])
def remove_game(game_id):
    delete_game(game_id)
    if state.game_id == game_id:
        clear_state()
    return redirect(url_for('index'))


@app.route('/api/state', methods=['GET'])
def api_state():
    return jsonify(game_payload())


def ensure_active_game(game_id=None):
    if game_id is not None and state.game_id != game_id:
        game = get_game(game_id)
        if not game:
            return False
        load_game_into_state(game, analyze=False)
    return state.started and state.game_id is not None


def apply_san_move(san):
    if not state.started:
        return False, 'Game not started.'
    san = (san or '').strip()
    if not san:
        return False, 'Enter a move in SAN, e.g. e4 or Nf6.'
    try:
        parsed = state.board.parse_san(san)
    except ValueError:
        return False, f'Invalid move: {san}'

    played = state.board.san(parsed)
    state.board.push(parsed)
    state.error = ''
    state.message = f'Played {played}.'
    persist_history()
    ended = game_over_message(state.board)
    if ended:
        state.message = ended
        state.suggestions = []
    else:
        refresh_suggestions()
    return True, None


@app.route('/api/move', methods=['POST'])
def play_move():
    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id') or request.args.get('game_id', type=int)
    if game_id is not None:
        game_id = int(game_id)
    if not ensure_active_game(game_id):
        return jsonify({'ok': False, 'error': 'Game not started.'}), 400

    ok, err = apply_san_move(payload.get('san', ''))
    if not ok:
        state.error = err
        body = game_payload()
        body['ok'] = False
        return jsonify(body), 400
    body = game_payload()
    body['ok'] = True
    return jsonify(body)


@app.route('/api/undo', methods=['POST'])
def undo():
    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id') or request.args.get('game_id', type=int)
    if game_id is not None:
        game_id = int(game_id)
    if not ensure_active_game(game_id):
        return jsonify({'ok': False, 'error': 'Game not started.'}), 400

    state.error = ''
    if not state.board.move_stack:
        state.error = 'No moves to undo.'
        body = game_payload()
        body['ok'] = False
        return jsonify(body), 400

    state.board.pop()
    state.message = 'Undid last move.'
    persist_history()
    refresh_suggestions()
    body = game_payload()
    body['ok'] = True
    return jsonify(body)


@app.route('/api/hint', methods=['POST'])
def hint():
    payload = request.get_json(silent=True) or {}
    game_id = payload.get('game_id') or request.args.get('game_id', type=int)
    if game_id is not None:
        game_id = int(game_id)
    if not ensure_active_game(game_id):
        return jsonify({'ok': False, 'error': 'Game not started.'}), 400

    state.error = ''
    if state.board.is_game_over():
        state.message = game_over_message(state.board)
    else:
        engine = get_engine()
        with _engine_lock:
            raw = analyze_position(engine, state.board, state.user_color)
        state.suggestions = [
            {
                **option,
                'first_move': option['moves'][0] if option['moves'] else '',
            }
            for option in raw
        ]
        state.message = 'Suggestions updated.' if state.suggestions else 'No suggestions for this position.'

    body = game_payload()
    body['ok'] = True
    return jsonify(body)


def main():
    # macOS AirPlay Receiver often binds :5000 and returns HTTP 403.
    port = 5050
    url = f'http://127.0.0.1:{port}'
    print(f'Chess move advisor UI: {url}')
    get_engine()
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
