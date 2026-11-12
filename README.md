# Chess Move Advisor

Інтерактивний шаховий асистент на Stockfish: обираєш сторону, вводиш ходи суперника, підтверджуєш свої, отримуєш топ-3 рекомендації.

Є **CLI**, **веб-UI** і **Docker** (Flask + PostgreSQL + Stockfish).

## Вимоги

- Docker + Docker Compose **або** Python 3.8+ з Stockfish у `PATH`
- Для локального запуску без Docker: `brew install stockfish`

## Швидкий старт (Docker)

```bash
cp .env.example .env
# за бажанням зміни POSTGRES_PASSWORD у .env

docker compose up -d --build
```

UI: [http://127.0.0.1:5050](http://127.0.0.1:5050)

Увійти (seed-користувач за замовчуванням):

- email: `hladii.b.v@gmail.com`
- пароль: `chess123` (зміни через **Profile**; у проді постав свій `SEED_USER_PASSWORD` / `SECRET_KEY` у `.env`)

### Міграція з SQLite → PostgreSQL

Якщо є старий файл `chess_assistant.db`:

```bash
# з хоста (Postgres уже опублікований на порту з .env)
export DATABASE_URL=postgresql://chess:change-me@127.0.0.1:5433/chess_assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/migrate_sqlite_to_postgres.py
```

Або всередині контейнера:

```bash
docker compose cp chess_assistant.db web:/app/chess_assistant.db
docker compose exec web python scripts/migrate_sqlite_to_postgres.py
```

Скрипт зберігає `id`, ім’я, історію, колір, FEN і дати.

## Локально без Docker (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://chess:chess@127.0.0.1:5433/chess_assistant
# підніми лише БД: docker compose up -d db
python app.py
```

CLI (аналіз без веб/БД):

```bash
python assistant.py
```

## Веб-UI

- список партій (**Continue** / **Delete**)
- нова партія (ім’я, сторона, опційна історія PGN/SAN)
- ходи суперника / свої в SAN, клікабельні Best moves, Hint / Undo
- кожен хід одразу зберігається в PostgreSQL

## Деплой на Oracle Cloud Always Free

1. Зареєструйся: [Oracle Cloud Free Tier](https://signup.cloud.oracle.com/)  
   Огляд: [oracle.com/cloud/free](https://www.oracle.com/cloud/free/)
2. Створи Always Free **Compute** VM (Ubuntu; Ampere A1 або AMD micro). Home Region обирається один раз.
3. У Security List / NSG відкрий **TCP 22** (SSH) і **TCP 5050** (або 80, якщо потім поставиш reverse proxy).
4. SSH на VM і встанови Docker:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
# перелогінься в SSH
```

5. Задеплой додаток:

```bash
git clone https://github.com/hladii/chess-assistant.git
cd chess-assistant
cp .env.example .env
# постав надійний POSTGRES_PASSWORD
docker compose up -d --build
```

6. Перенеси дані: скопіюй `chess_assistant.db` на VM і запусти міграцію (див. вище).

7. Відкрий `http://<PUBLIC_IP>:5050`.

> Always Free VM не засинає. Не апгрейдь акаунт у платний, якщо хочеш лишатися на безкоштовному Always Free після trial.

## Структура

| Файл | Призначення |
|------|-------------|
| `app.py` | Flask веб-UI |
| `auth.py` | логін, CSRF, хеш паролів (scrypt) |
| `assistant.py` | CLI + логіка аналізу |
| `db.py` | PostgreSQL (`users`, `games`) |
| `Dockerfile` / `docker-compose.yml` | контейнери web + Postgres |
| `scripts/migrate_sqlite_to_postgres.py` | міграція зі старої SQLite |
| `templates/` / `static/` | HTML / CSS / JS |

## Ліцензія

Особистий проєкт — використовуй як зручно.
