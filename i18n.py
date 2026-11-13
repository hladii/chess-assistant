"""Locale helpers and JS message catalog for Chess Move Advisor."""

from flask import request, session
from flask_babel import gettext as _

SUPPORTED_LOCALES = ('uk', 'en', 'it')
DEFAULT_LOCALE = 'en'
LOCALE_COOKIE = 'locale'
LOCALE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60

LOCALE_LABELS = {
    'uk': 'Українська',
    'en': 'English',
    'it': 'Italiano',
}


def normalize_locale(value):
    code = (value or '').strip().lower().replace('_', '-')
    if not code:
        return None
    primary = code.split('-', 1)[0]
    if primary in SUPPORTED_LOCALES:
        return primary
    return None


def resolve_locale(user=None):
    """Logged-in user locale → session → cookie → English default."""
    if user:
        loc = normalize_locale(user.get('locale'))
        if loc:
            return loc
    loc = normalize_locale(session.get('locale'))
    if loc:
        return loc
    loc = normalize_locale(request.cookies.get(LOCALE_COOKIE))
    if loc:
        return loc
    return DEFAULT_LOCALE


def set_session_locale(locale):
    loc = normalize_locale(locale) or DEFAULT_LOCALE
    session['locale'] = loc
    return loc


def js_messages():
    """Strings used by static/app.js and static/nav.js."""
    return {
        'openMenu': _('Open menu'),
        'closeMenu': _('Close menu'),
        'noMovesYet': _('No moves yet.'),
        'colNum': '#',
        'colWhite': _('White'),
        'colBlack': _('Black'),
        'betterWas': _('Better was %(move)s'),
        'pressHint': _('Press Hint for engine lines.'),
        'enterOpponent': _('Enter the opponent move, then your turn suggestions appear.'),
        'illegalMove': _('Illegal move.'),
        'enterSan': _('Enter a move in SAN, e.g. e4 or Nf6.'),
        'requestFailed': _('Request failed. Try again.'),
        'working': _('Working…'),
        'checkingMove': _('Checking your move…'),
        'analyzingPosition': _('Analyzing position…'),
        'submittingOpponent': _('Submitting opponent move…'),
        'playingMove': _('Playing move…'),
        'playingSelected': _('Playing selected move…'),
        'analyzing': _('Analyzing…'),
        'undoing': _('Undoing…'),
        'trainer': _('Trainer'),
        'assistant': _('Assistant'),
        'duel': _('Duel'),
        'move': _('Move'),
        'sanSuffix': _('(SAN)'),
        'pressAnalyze': _('Press Analyze for engine lines. Click a move to inspect that position.'),
        'confirmResign': _('Resign this duel?'),
        'resigning': _('Resigning…'),
        'classification_best': _('Best'),
        'classification_inaccuracy': _('Inaccuracy'),
        'classification_mistake': _('Mistake'),
        'classification_blunder': _('Blunder'),
    }
