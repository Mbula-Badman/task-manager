import os, sys, tempfile, unittest
from unittest.mock import patch
from uuid import uuid4

# Tests must never touch a real database, even when a .env file exists:
# empty values stop python-dotenv from filling these in.
os.environ["DATABASE_URL"] = ""
os.environ["DB_PASSWORD"] = ""
os.environ["TASK_MANAGER_USE_SQLITE"] = "1"
os.environ["TASKS_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as m


class TaskAPITests(unittest.TestCase):
    def test_missing_database_url_does_not_silently_use_sqlite(self):
        with patch.dict(os.environ, {"DATABASE_URL": "", "TASK_MANAGER_USE_SQLITE": "0"}):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL is not set"):
                m.connect_db()

    def test_postgres_settings_from_parts(self):
        with patch.dict(os.environ, {"DATABASE_URL": "", "DB_PASSWORD": "p@ss word"}):
            args = m.postgres_connect_args()
        self.assertEqual((args["host"], args["dbname"], args["password"]), ("localhost", "task_manager", "p@ss word"))

    def setUp(self):
        self.c = m.app.test_client()
        with m.app.app_context():
            self.assertFalse(m.get_db().postgres, "Tests must run on SQLite, never on PostgreSQL.")
            m.get_db().execute("DELETE FROM tasks")
            m.get_db().commit()
        self.email = f"{uuid4().hex}@example.com"
        self.c.get("/register")
        self.csrf_token = self.session_csrf(self.c)
        response = self.c.post("/register", data={
            "email": self.email, "password": "password123", "_csrf_token": self.csrf_token,
        })
        self.assertEqual(response.status_code, 302)
        self.csrf_token = self.session_csrf(self.c)

    @staticmethod
    def session_csrf(client):
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def api_headers(self):
        return {"X-CSRF-Token": self.csrf_token}

    def test_crud_cycle(self):
        r = self.c.post("/api/tasks", json={"title": "Lab", "category": "Security Lab", "priority": "high"},
                        headers=self.api_headers())
        self.assertEqual(r.status_code, 201)
        tid = r.json["id"]
        self.assertEqual(
            self.c.put(f"/api/tasks/{tid}", json={"status": "done"}, headers=self.api_headers()).json["done"],
            True,
        )
        self.assertEqual(self.c.delete(f"/api/tasks/{tid}", headers=self.api_headers()).status_code, 204)
        self.assertEqual(
            self.c.delete(f"/api/tasks/{tid}", headers=self.api_headers()).status_code, 404
        )

    def test_validation(self):
        for bad in ({"title": " "}, {"title": "x", "category": "Nope"},
                    {"title": "x", "status": "later"}, {"title": "x", "due_date": "tomorrow"}):
            self.assertEqual(
                self.c.post("/api/tasks", json=bad, headers=self.api_headers()).status_code, 400
            )

    def test_filters_and_stats(self):
        headers = self.api_headers()
        self.c.post("/api/tasks", json={"title": "a", "category": "Business", "due_date": "2000-01-01"},
                    headers=headers)
        self.c.post("/api/tasks", json={"title": "b", "category": "Projects", "status": "done"},
                    headers=headers)
        self.assertEqual(len(self.c.get("/api/tasks?category=Business").json), 1)
        s = self.c.get("/api/stats").json
        self.assertEqual((s["total"], s["overdue"], s["completion"]), (2, 1, 50))

    def test_seed_and_csv(self):
        self.assertEqual(self.c.post("/api/seed", headers=self.api_headers()).status_code, 201)
        self.assertEqual(self.c.post("/api/seed", headers=self.api_headers()).status_code, 409)
        csv_text = self.c.get("/api/export.csv").data.decode()
        self.assertTrue(csv_text.startswith("title,category"))

    def test_csv_formula_guard(self):
        self.c.post("/api/tasks", json={"title": "=HYPERLINK(1)"}, headers=self.api_headers())
        self.assertIn("'=HYPERLINK", self.c.get("/api/export.csv").data.decode())

    def test_login_logout_and_private_tasks(self):
        created = self.c.post("/api/tasks", json={"title": "Private task"}, headers=self.api_headers())
        task_id = created.json["id"]
        self.assertEqual(self.c.post("/logout", data={"_csrf_token": self.csrf_token}).status_code, 302)
        self.assertEqual(self.c.get("/api/tasks").status_code, 401)

        second = m.app.test_client()
        second.get("/register")
        second_token = self.session_csrf(second)
        self.assertEqual(second.post("/register", data={
            "email": f"{uuid4().hex}@example.com", "password": "different123",
            "_csrf_token": second_token,
        }).status_code, 302)
        self.assertEqual(second.get("/api/tasks").json, [])
        self.assertEqual(second.delete(
            f"/api/tasks/{task_id}", headers={"X-CSRF-Token": self.session_csrf(second)}
        ).status_code, 404)

        self.c.get("/login")
        token = self.session_csrf(self.c)
        response = self.c.post("/login", data={
            "email": self.email.upper(), "password": "password123", "_csrf_token": token,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.c.get("/api/tasks").json), 1)

    def test_registration_validation_and_csrf(self):
        client = m.app.test_client()
        client.get("/register")
        token = self.session_csrf(client)
        self.assertEqual(client.post("/register", data={
            "email": f"{uuid4().hex}@example.com", "password": "short", "_csrf_token": token,
        }).status_code, 200)
        self.assertEqual(client.post("/register", data={
            "email": f"{uuid4().hex}@example.com", "password": "password123",
        }).status_code, 400)


if __name__ == "__main__":
    unittest.main()
