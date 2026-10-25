document.addEventListener('DOMContentLoaded', () => {
  const board = document.getElementById('board');
  if (!board) return;

  const csrfToken = document.getElementById('csrf-token')?.value || '';

  function renderTask(task) {
    const card = document.createElement('article');
    card.className = 'task-card';
    card.dataset.priority = task.priority;
    card.dataset.id = task.id;

    const title = document.createElement('h4');
    title.textContent = task.title;

    const meta = document.createElement('div');
    meta.className = 'task-meta';
    meta.textContent = `${task.category} • ${task.priority} • ${task.status}`;

    const notes = task.notes ? document.createElement('p') : null;
    if (notes) {
      notes.textContent = task.notes;
    }

    const actions = document.createElement('div');
    actions.className = 'task-actions';

    const nextStatus = task.status === 'todo' ? 'doing' : task.status === 'doing' ? 'done' : 'todo';
    const advanceButton = document.createElement('button');
    advanceButton.type = 'button';
    advanceButton.textContent = task.status === 'done' ? 'Reopen' : 'Advance';
    advanceButton.className = 'secondary';
    advanceButton.addEventListener('click', () => updateTask(task.id, { status: nextStatus }));

    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.textContent = 'Delete';
    deleteButton.className = 'danger';
    deleteButton.addEventListener('click', () => deleteTask(task.id));

    actions.appendChild(advanceButton);
    actions.appendChild(deleteButton);

    card.append(title, meta);
    if (notes) card.append(notes);
    card.append(actions);
    return card;
  }

  function loadTasks() {
    fetch('/api/tasks', { credentials: 'same-origin' })
      .then((response) => response.json())
      .then((tasks) => {
        ['todo', 'doing', 'done'].forEach((status) => {
          const list = document.getElementById(`${status}-list`);
          if (!list) return;
          list.innerHTML = '';
          tasks.filter((task) => task.status === status).forEach((task) => list.appendChild(renderTask(task)));
        });
      })
      .catch((error) => console.error('Failed to load tasks:', error));
  }

  function apiRequest(url, method, payload) {
    return fetch(url, {
      method,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: payload ? JSON.stringify(payload) : null,
    }).then((response) => {
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }
      return response;
    });
  }

  function updateTask(taskId, payload) {
    apiRequest(`/api/tasks/${taskId}`, 'PUT', payload)
      .then(() => loadTasks())
      .catch((error) => console.error('Update failed:', error));
  }

  function deleteTask(taskId) {
    apiRequest(`/api/tasks/${taskId}`, 'DELETE', null)
      .then(() => loadTasks())
      .catch((error) => console.error('Delete failed:', error));
  }

  const form = document.getElementById('task-form');
  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const data = {
        title: document.getElementById('title').value.trim(),
        category: document.getElementById('category').value,
        priority: document.getElementById('priority').value,
        status: document.getElementById('status').value,
        due_date: document.getElementById('due_date').value || null,
        notes: document.getElementById('notes').value.trim(),
      };

      if (!data.title) {
        return;
      }

      apiRequest('/api/tasks', 'POST', data)
        .then(() => {
          form.reset();
          loadTasks();
        })
        .catch((error) => console.error('Create task failed:', error));
    });
  }

  loadTasks();
});
