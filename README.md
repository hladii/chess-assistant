# Chess Move Advisor

Інтерактивний шаховий асистент на Stockfish: обираєш сторону, вводиш ходи суперника, підтверджуєш свої, отримуєш топ-3 рекомендації.

Є **CLI** і мінімальний **веб-UI** зі збереженням партій у SQLite.

## Вимоги

- Python 3.8+
- [Stockfish](https://stockfishchess.org/) у `PATH`

```bash
brew install stockfish   # macOS
```

## Встановлення

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Веб-UI

```bash
python app.py
```

Відкриється [http://127.0.0.1:5050](http://127.0.0.1:5050) (порт `5050`, бо на macOS `5000` часто зайнятий AirPlay).

На головній сторінці:

- список збережених партій (**Continue** / **Delete**)
- створення нової партії (ім’я, сторона, опційна історія PGN/SAN)

Під час гри:

- хід суперника / свій хід у SAN
- клікабельні **Best moves**
- **Hint**, **Undo**
- історія ходів; кожен крок одразу пишеться в SQLite (`chess_assistant.db`)

## CLI

```bash
python assistant.py
```

1. Вибери сторону (`w` / `b`)
2. За бажанням завантаж історію (PGN або SAN)
3. Вводь ходи суперника; підтверджуй свої SAN-ом
4. Команди: `hint`, `undo`, `board`, `help`, `quit`

## Структура

| Файл | Призначення |
|------|-------------|
| `app.py` | Flask веб-UI |
| `assistant.py` | CLI + спільна логіка аналізу |
| `db.py` | SQLite (таблиця `games`) |
| `templates/` | HTML |
| `static/` | CSS / JS |

## Ліцензія

Особистий проєкт — використовуй як зручно.
