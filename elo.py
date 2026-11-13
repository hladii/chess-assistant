"""Internal Elo rating with logistic (regression) expected-score formula."""

DEFAULT_ELO = 1200
K_FACTOR = 32


def expected_score(rating_a, rating_b):
    """Probability that A scores against B: 1 / (1 + 10^((Rb-Ra)/400))."""
    return 1.0 / (1.0 + 10.0 ** ((float(rating_b) - float(rating_a)) / 400.0))


def rating_delta(rating_a, rating_b, score_a, k=K_FACTOR):
    """Integer Elo change for player A given actual score (1 / 0.5 / 0)."""
    expected = expected_score(rating_a, rating_b)
    return int(round(k * (float(score_a) - expected)))


def pair_updates(rating_white, rating_black, result):
    """
    Return (new_white, new_black, delta_white, delta_black) for a finished game.

    result: '1-0' | '0-1' | '1/2-1/2'
    """
    if result == '1-0':
        score_white = 1.0
    elif result == '0-1':
        score_white = 0.0
    elif result == '1/2-1/2':
        score_white = 0.5
    else:
        raise ValueError(f'Invalid result: {result}')

    score_black = 1.0 - score_white
    dw = rating_delta(rating_white, rating_black, score_white)
    db = rating_delta(rating_black, rating_white, score_black)
    return rating_white + dw, rating_black + db, dw, db


def result_from_board(board):
    """Map a terminal python-chess board to a PGN-style result, or None."""
    if not board.is_game_over():
        return None
    if board.is_checkmate():
        # Side to move is mated.
        return '0-1' if board.turn else '1-0'
    return '1/2-1/2'


def result_from_resign(resigner_color):
    """resigner_color: chess.WHITE (True) or chess.BLACK (False) / 'w'|'b'."""
    if resigner_color in (True, 'w', 'white', 'W'):
        return '0-1'
    return '1-0'
