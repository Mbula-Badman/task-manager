# Task Manager App

A multi-user workflow tracker built with Flask, vanilla JavaScript, and PostgreSQL.
Tasks are organized by category and move through a To do / In progress / Done pipeline.

## Features
- Account registration and login with hashed passwords
- Private tasks, stats, and CSV exports for each account
- Full CRUD with validation (title, notes, category, priority, due date, status)
- Stats dashboard, list view, and drag-and-drop Kanban board
- Filter by status/category, search, CSV export, and sample data
- PostgreSQL through `DATABASE_URL`; SQLite is available only when explicitly enabled

## Run with PostgreSQL

1. Create the database once (SQL Shell / psql): `CREATE DATABASE task_manager;`
2. Open the `.env` file next to `app.py` and replace `PUT_YOUR_POSTGRES_PASSWORD_HERE` with your
   `postgres` password (keep the quotes). Optional settings: `DB_HOST`, `DB_PORT`, `DB_NAME`
   (default `task_manager`) and `DB_USER` (default `postgres`).
3. Install and run:

```powershell
pip install -r requirements.txt
python app.py
```

The app reads `.env` automatically, creates its tables on startup and logs which database it
opened. If PostgreSQL is unreachable it stops with a plain explanation instead of a traceback.
Instead of the `DB_*` settings you can set a single `DATABASE_URL`
(`postgresql://USER:PASSWORD@localhost:5432/task_manager`, URL-encode special characters).

Never commit `.env`. `SECRET_KEY` in `.env` keeps you logged in across restarts; generate a new
one for any real deployment. Existing SQLite tasks are not copied into PostgreSQL.

For intentional SQLite use (for example, local testing), set `TASK_MANAGER_USE_SQLITE=1`
(and make sure `DATABASE_URL`/`DB_PASSWORD` are empty). The file defaults to `tasks.db`;
change it with `TASKS_DB`.

## Test
```powershell
python -m unittest discover -s tests
```

## API

All API routes require a signed-in session. Mutating requests also require the CSRF token
sent by the app's frontend.

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/tasks` | List tasks (`status`, `category`, `q` filters) |
| POST | `/api/tasks` | Create a task |
| PUT | `/api/tasks/<id>` | Update a task |
| DELETE | `/api/tasks/<id>` | Delete a task |
| GET | `/api/stats` | Counts, completion %, overdue, and due soon |
| GET | `/api/export.csv` | Download the signed-in user's tasks |
| POST | `/api/seed` | Load sample tasks if the signed-in user has none |
