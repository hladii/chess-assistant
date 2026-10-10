import atexit
import threading
import webbrowser
from dataclasses import dataclass, field

import chess
import chess.svg
from flask import Flask, jsonify, redirect, render_template, request, url_for

from assistant import (
    analyze_position,
    find_engine,
    game_over_message,
    parse_history,
)

app = Flask(__name__)

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


def format_move_history(board, start_fen):
    replay = chess.Board(start_fen)
    parts = []
    for move in board.move_stack:
        if replay.turn == chess.WHITE:
            parts.append(f'{replay.fullmove_number}.')
        parts.append(replay.san(move))
        replay.push(move)
    return ' '.join(parts)


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


def game_payload():
    ended = game_over_message(state.board) if state.started else None
    if ended:
        state.message = ended
    your_turn = state.started and state.board.turn == state.user_color and not ended
    return {
        'started': state.started,
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


def view_model():
    return game_payload()


def wants_json():
    if request.args.get('format') == 'json':
        return True
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept.best == 'application/json'


@app.route('/')
def index():
    return render_template('index.html', **view_model())


@app.route('/api/state', methods=['GET'])
def api_state():
    return jsonify(game_payload())


@app.route('/start', methods=['POST'])
def start():
    side = request.form.get('side', 'w').lower()
    history = request.form.get('history', '')
    state.user_color = chess.WHITE if side in ('w', 'white') else chess.BLACK
    state.error = ''
    state.message = ''
    try:
        board = parse_history(history)
    except ValueError as exc:
        state.error = f'Could not parse history: {exc}'
        state.started = False
        return redirect(url_for('index'))

    replay_start = board.copy()
    while replay_start.move_stack:
        replay_start.pop()
    state.start_fen = replay_start.fen()
    state.board = board
    state.started = True
    state.suggestions = []
    state.message = f'You play as {side_label(state.user_color)}.'
    refresh_suggestions()
    return redirect(url_for('index'))


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
    ended = game_over_message(state.board)
    if ended:
        state.message = ended
        state.suggestions = []
    else:
        refresh_suggestions()
    return True, None


@app.route('/move', methods=['POST'], endpoint='move')
@app.route('/api/move', methods=['POST'])
def play_move():
    if request.is_json:
        san = (request.get_json(silent=True) or {}).get('san', '')
    else:
        san = request.form.get('san', '')

    ok, err = apply_san_move(san)
    if not ok:
        state.error = err
        if wants_json() or request.path.startswith('/api/'):
            payload = game_payload()
            payload['ok'] = False
            return jsonify(payload), 400
        return redirect(url_for('index'))

    if wants_json() or request.path.startswith('/api/'):
        payload = game_payload()
        payload['ok'] = True
        return jsonify(payload)
    return redirect(url_for('index'))


@app.route('/undo', methods=['POST'])
@app.route('/api/undo', methods=['POST'])
def undo():
    if not state.started:
        if request.path.startswith('/api/') or wants_json():
            return jsonify({'ok': False, 'error': 'Game not started.'}), 400
        return redirect(url_for('index'))

    state.error = ''
    if not state.board.move_stack:
        state.error = 'No moves to undo.'
        if request.path.startswith('/api/') or wants_json():
            payload = game_payload()
            payload['ok'] = False
            return jsonify(payload), 400
        return redirect(url_for('index'))

    state.board.pop()
    state.message = 'Undid last move.'
    refresh_suggestions()
    if request.path.startswith('/api/') or wants_json():
        payload = game_payload()
        payload['ok'] = True
        return jsonify(payload)
    return redirect(url_for('index'))


@app.route('/hint', methods=['POST'])
@app.route('/api/hint', methods=['POST'])
def hint():
    if not state.started:
        if request.path.startswith('/api/') or wants_json():
            return jsonify({'ok': False, 'error': 'Game not started.'}), 400
        return redirect(url_for('index'))

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

    if request.path.startswith('/api/') or wants_json():
        payload = game_payload()
        payload['ok'] = True
        return jsonify(payload)
    return redirect(url_for('index'))


@app.route('/new', methods=['POST'])
def new_game():
    state.board = chess.Board()
    state.start_fen = chess.STARTING_FEN
    state.started = False
    state.suggestions = []
    state.message = ''
    state.error = ''
    return redirect(url_for('index'))


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
