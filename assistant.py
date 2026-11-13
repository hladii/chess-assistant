import io
import shutil
from pathlib import Path

import chess
import chess.engine
import chess.pgn


COMMANDS = {'undo', 'board', 'hint', 'resign', 'quit', 'help'}
ANALYSIS_TIME = 3.0
ANALYSIS_MULTIPV = 3
TRAINER_ANALYSIS_TIME = 0.8
TRAINER_MOVE_TIME = 0.4
TRAINER_ELO_LEVELS = (800, 1200, 1600)


def find_engine():
    candidates = [
        shutil.which('stockfish'),
        '/usr/games/stockfish',  # Debian/Ubuntu package
        '/usr/local/bin/stockfish',
        '/opt/homebrew/bin/stockfish',
    ]
    engine_path = next((path for path in candidates if path and Path(path).is_file()), None)
    if not engine_path:
        raise RuntimeError('Stockfish not found. Install it with: brew install stockfish')
    return chess.engine.SimpleEngine.popen_uci(engine_path)


def board_from_san_list(text):
    board = chess.Board()
    tokens = text.replace('\n', ' ').split()
    for token in tokens:
        if token.endswith('.'):
            continue
        if token in ('*', '1-0', '0-1', '1/2-1/2'):
            break
        board.push_san(token)
    return board


def parse_history(text):
    """Parse PGN or SAN move list into a Board. Empty text -> starting position."""
    text = (text or '').strip()
    if not text:
        return chess.Board()

    game = chess.pgn.read_game(io.StringIO(text))
    if game is not None:
        board = game.board()
        moves = list(game.mainline_moves())
        if moves:
            for move in moves:
                board.push(move)
            return board

    return board_from_san_list(text)


def board_from_saved_history(start_fen, history_text):
    """Replay stored SAN history from a known starting FEN."""
    board = chess.Board(start_fen or chess.STARTING_FEN)
    text = (history_text or '').strip()
    if not text:
        return board
    tokens = text.replace('\n', ' ').split()
    for token in tokens:
        if token.endswith('.'):
            continue
        if token in ('*', '1-0', '0-1', '1/2-1/2'):
            break
        board.push_san(token)
    return board


def format_score(score):
    if score.is_mate():
        mate = score.mate()
        return f'mate in {mate}' if mate and mate > 0 else f'mated in {abs(mate)}'
    return f'{score.score() / 100:.2f}'


def analyze_position(engine, board, user_color, time_limit=ANALYSIS_TIME, multipv=ANALYSIS_MULTIPV):
    """Return list of {moves, evaluation} from the user's POV."""
    if board.is_game_over():
        return []

    set_engine_unlimited(engine)
    results = engine.analyse(
        board,
        chess.engine.Limit(time=time_limit),
        multipv=multipv,
    )
    if not isinstance(results, list):
        results = [results]

    options = []
    for result in results:
        score = result['score'].pov(user_color)
        line_board = board.copy()
        moves = []
        plies = []
        for move in result.get('pv', [])[:8]:
            san = line_board.san(move)
            side = 'you' if line_board.turn == user_color else 'opponent'
            moves.append(san)
            plies.append({'san': san, 'side': side})
            line_board.push(move)
        options.append({
            'moves': moves,
            'plies': plies,
            'line': ' '.join(moves),
            'evaluation': format_score(score),
        })
    return options


def set_engine_unlimited(engine):
    try:
        engine.configure({'UCI_LimitStrength': False})
    except chess.engine.EngineError:
        pass


def configure_engine_strength(engine, elo):
    elo = int(elo)
    if elo not in TRAINER_ELO_LEVELS:
        elo = 1200
    try:
        engine.configure({
            'UCI_LimitStrength': True,
            'UCI_Elo': elo,
        })
    except chess.engine.EngineError:
        # Older Stockfish builds may not expose UCI_Elo the same way.
        try:
            engine.configure({'Skill Level': max(0, min(20, elo // 150))})
        except chess.engine.EngineError:
            pass
    return elo


def score_to_cp(score):
    """Convert a PovScore/Score to centipawns; mates become large signed values."""
    if score.is_mate():
        mate = score.mate()
        if mate is None:
            return 0
        # Prefer faster mates as larger magnitude.
        return (100000 - abs(mate) * 100) * (1 if mate > 0 else -1)
    value = score.score()
    return 0 if value is None else value


def classify_cp_loss(loss):
    if loss is None or loss <= 30:
        return 'best'
    if loss <= 100:
        return 'inaccuracy'
    if loss <= 300:
        return 'mistake'
    return 'blunder'


QUALITY_POINTS = {
    'best': 100,
    'inaccuracy': 80,
    'mistake': 50,
    'blunder': 20,
}


def classification_to_points(classification):
    return QUALITY_POINTS.get(classification, 20)


def coach_message(classification, played_san, best_san, cp_before, cp_after, cp_loss):
    before = f'{cp_before / 100:.2f}'
    after = f'{cp_after / 100:.2f}'
    loss = f'{cp_loss / 100:.2f}'
    if classification == 'best':
        return f'Good move ({played_san}). Eval {before} → {after}.'
    if best_san and best_san != played_san:
        return (
            f'{classification.title()}: {played_san} (eval {before} → {after}, '
            f'loss {loss}). Better was {best_san}.'
        )
    return f'{classification.title()}: {played_san} (eval {before} → {after}, loss {loss}).'


def evaluate_user_move(engine, board_before, move, user_color, time_limit=TRAINER_ANALYSIS_TIME):
    """Classify a played move vs the engine best move. board_before is prior to move."""
    set_engine_unlimited(engine)
    before_info = engine.analyse(board_before, chess.engine.Limit(time=time_limit))
    before_score = before_info['score'].pov(user_color)
    best_move = None
    best_san = ''
    pv = before_info.get('pv') or []
    if pv:
        best_move = pv[0]
        best_san = board_before.san(best_move)

    played_san = board_before.san(move)
    board_after = board_before.copy()
    board_after.push(move)

    after_info = engine.analyse(board_after, chess.engine.Limit(time=time_limit))
    after_score = after_info['score'].pov(user_color)

    cp_before = score_to_cp(before_score)
    cp_after = score_to_cp(after_score)
    cp_loss = max(0, cp_before - cp_after)
    if best_move is not None and move == best_move:
        classification = 'best'
        cp_loss = 0
    else:
        classification = classify_cp_loss(cp_loss)

    return {
        'classification': classification,
        'played_san': played_san,
        'best_move_san': best_san,
        'cp_before': cp_before,
        'cp_after': cp_after,
        'cp_loss': cp_loss,
        'eval_before': format_score(before_score),
        'eval_after': format_score(after_score),
        'message': coach_message(
            classification, played_san, best_san, cp_before, cp_after, cp_loss
        ),
    }


def engine_reply(engine, board, elo, time_limit=TRAINER_MOVE_TIME):
    """Play one move as the trainer opponent at limited strength. Returns SAN."""
    if board.is_game_over():
        return None
    configure_engine_strength(engine, elo)
    result = engine.play(board, chess.engine.Limit(time=time_limit))
    set_engine_unlimited(engine)
    if not result.move:
        return None
    return board.san(result.move)


def game_over_message(board):
    if not board.is_game_over():
        return None
    if board.is_checkmate():
        winner = 'Black' if board.turn == chess.WHITE else 'White'
        return f'Checkmate. {winner} wins.'
    if board.is_stalemate():
        return 'Stalemate. Draw.'
    if board.is_insufficient_material():
        return 'Draw by insufficient material.'
    return f'Game over: {board.result()}'


def parse_move_or_command(board, text):
    lowered = text.lower()
    if lowered in COMMANDS:
        return ('command', lowered)
    try:
        move = board.parse_san(text)
        return ('move', move)
    except ValueError:
        return ('error', f'Invalid move or command: {text}')


def choose_side():
    while True:
        choice = input('Choose your side (w/b): ').strip().lower()
        if choice in ('w', 'white'):
            return chess.WHITE
        if choice in ('b', 'black'):
            return chess.BLACK
        print('Please enter w or b.')


def ask_yes_no(prompt):
    while True:
        answer = input(prompt).strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print('Please enter y or n.')


def read_multiline(prompt):
    print(prompt)
    print('Enter an empty line to finish.\n')
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def load_history():
    text = read_multiline('Paste PGN or a list of moves (SAN).')
    if not text:
        print('No history entered. Starting from the initial position.')
        return chess.Board()
    try:
        return parse_history(text)
    except ValueError as exc:
        print(f'Could not parse history: {exc}')
        print('Starting from the initial position.')
        return chess.Board()


def print_board(board):
    print()
    print(board)
    side = 'White' if board.turn == chess.WHITE else 'Black'
    print(f'Side to move: {side}')


def suggest_moves(engine, board, user_color):
    print('\nAnalyzing...')
    for i, option in enumerate(analyze_position(engine, board, user_color), start=1):
        print(f'\nOption {i}: {option["line"]}')
        print(f'Evaluation: {option["evaluation"]}')


def read_user_input(prompt):
    raw = input(prompt).strip()
    if not raw:
        return None
    return raw


def print_help():
    print('Commands: undo, board, hint, resign, quit, help')
    print('Moves: enter SAN notation, e.g. e4, Nf6, O-O')


def handle_command(command, board, engine, user_color):
    """Apply a command. Returns (done, need_hint)."""
    if command in ('quit', 'resign'):
        print('Game ended.')
        return True, False
    if command == 'help':
        print_help()
        return False, False
    if command == 'board':
        print_board(board)
        return False, False
    if command == 'hint':
        suggest_moves(engine, board, user_color)
        return False, False
    if command == 'undo':
        if board.move_stack:
            board.pop()
            print_board(board)
            return False, board.turn == user_color
        print('No moves to undo.')
        return False, False
    return False, False


def play_game(engine):
    user_color = choose_side()
    user_name = 'White' if user_color == chess.WHITE else 'Black'
    print(f'You play as {user_name}.')

    if ask_yes_no('Load game history? (y/n): '):
        board = load_history()
    else:
        board = chess.Board()

    print_board(board)
    need_hint = board.turn == user_color and not board.is_game_over()

    while True:
        ended = game_over_message(board)
        if ended:
            print(ended)
            break

        prompt = 'Your move: ' if board.turn == user_color else 'Opponent move: '
        if board.turn == user_color and need_hint:
            suggest_moves(engine, board, user_color)
            need_hint = False

        raw = read_user_input(prompt)
        if raw is None:
            continue

        kind, value = parse_move_or_command(board, raw)
        if kind == 'error':
            print(value)
            print_help()
            continue
        if kind == 'command':
            done, hint_after = handle_command(value, board, engine, user_color)
            if done:
                break
            if hint_after:
                need_hint = True
            continue

        board.push(value)
        print_board(board)
        if board.turn == user_color:
            need_hint = True


def main():
    engine = find_engine()
    try:
        print('Chess move advisor')
        print('Type help during a game for commands.\n')
        while True:
            play_game(engine)
            if not ask_yes_no('Start another game? (y/n): '):
                break
            print()
    finally:
        engine.quit()


if __name__ == '__main__':
    main()
