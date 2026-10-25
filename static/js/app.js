document.addEventListener('DOMContentLoaded', () => {
  const board = document.getElementById('board');
  if (!board) return;

  const csrfToken = document.getElementById('csrf-token')?.value || '';
  const message = document.getElementById('app-message');
  const search = document.getElementById('task-search');
  const categoryFilter = document.getElementById('filter-category');
  const statusFilter = document.getElementById('filter-status');
  const seedButton = document.getElementById('seed-tasks');
  const statusLabels = { todo: 'To do', doing: 'In progress', done: 'Done' };
  let searchTimer;

  function showMessage(text, success = false) {
    message.textContent = text;
    message.classList.toggle('success', success);
    message.hidden = !text;
  }

  async function apiRequest(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'X-CSRF-Token': csrfToken,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try {
        const body = await response.json();
        if (body.error) detail = body.error;
      } catch (error) {
        console.error('Could not read the server error response:', error);
      }
      throw new Error(detail);
    }
    return response;
  }

  function dateLabel(value) {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  function isoDate(value) {
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
  }

  function renderTask(task) {
    const card = document.createElement('article');
    card.className = 'task-card';
    card.dataset.priority = task.priority;
    card.dataset.id = task.id;

    const title = document.createElement('h4');
    title.textContent = task.title;

    const meta = document.createElement('div');
    meta.className = 'task-meta';
    [task.category, task.priority[0].toUpperCase() + task.priority.slice(1)].forEach((label) => {
      const tag = document.createElement('span');
      tag.className = 'task-tag';
      tag.textContent = label;
      meta.appendChild(tag);
    });

    if (task.due_date) {
      const dueTag = document.createElement('span');
      const todayDate = new Date();
      const today = isoDate(todayDate);
      const dueSoonDate = new Date(todayDate);
      dueSoonDate.setDate(dueSoonDate.getDate() + 3);
      const isOverdue = task.status !== 'done' && task.due_date < today;
      const isDueSoon = task.status !== 'done' && task.due_date >= today &&
        task.due_date <= isoDate(dueSoonDate);
      dueTag.className = `task-tag${isOverdue ? ' overdue' : isDueSoon ? ' due-soon' : ''}`;
      dueTag.textContent = `${isOverdue ? 'Overdue · ' : 'Due · '}${dateLabel(task.due_date)}`;
      meta.appendChild(dueTag);
    }

    const notes = task.notes ? document.createElement('p') : null;
    if (notes) notes.textContent = task.notes;

    const actions = document.createElement('div');
    actions.className = 'task-actions';

    const nextStatus = task.status === 'todo' ? 'doing' : task.status === 'doing' ? 'done' : 'todo';
    const advanceButton = document.createElement('button');
    advanceButton.type = 'button';
    advanceButton.textContent = task.status === 'done' ? 'Reopen' : `Move to ${statusLabels[nextStatus]}`;
    advanceButton.className = 'secondary';
    advanceButton.addEventListener('click', () => updateTask(task.id, { status: nextStatus }));

    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.textContent = 'Delete';
    deleteButton.className = 'danger';
    deleteButton.addEventListener('click', () => {
      if (window.confirm(`Delete "${task.title}"? This can’t be undone.`)) deleteTask(task.id);
    });

    actions.append(advanceButton, deleteButton);
    card.append(title, meta);
    if (notes) card.appendChild(notes);
    card.appendChild(actions);
    return card;
  }

  async function loadStats() {
    const response = await fetch('/api/stats', { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Could not load task summary (${response.status}).`);
    const stats = await response.json();
    document.getElementById('stat-total').textContent = stats.total;
    document.getElementById('stat-doing').textContent = stats.doing;
    document.getElementById('stat-completion').textContent = `${stats.completion}%`;
    document.getElementById('stat-done').textContent = stats.done;
    document.getElementById('stat-overdue').textContent = stats.overdue;
    document.getElementById('stat-due-soon').textContent = stats.due_soon;
    seedButton.hidden = stats.total > 0;
  }

  async function loadTasks() {
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q', search.value.trim());
    if (categoryFilter.value !== 'all') params.set('category', categoryFilter.value);
    if (statusFilter.value !== 'all') params.set('status', statusFilter.value);

    const response = await fetch(`/api/tasks?${params}`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Could not load tasks (${response.status}).`);
    const tasks = await response.json();

    ['todo', 'doing', 'done'].forEach((status) => {
      const list = document.getElementById(`${status}-list`);
      const statusTasks = tasks.filter((task) => task.status === status);
      list.replaceChildren(...statusTasks.map(renderTask));
      document.getElementById(`${status}-count`).textContent = statusTasks.length;
      if (statusTasks.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'empty-state';
        empty.textContent = tasks.length === 0
          ? 'No matching tasks. Try another filter or add a new task.'
          : 'Nothing here just yet.';
        list.appendChild(empty);
      }
    });
  }

  async function refreshDashboard() {
    try {
      await Promise.all([loadTasks(), loadStats()]);
    } catch (error) {
      console.error('Failed to refresh the dashboard:', error);
      showMessage(error.message);
    }
  }

  async function updateTask(taskId, payload) {
    try {
      await apiRequest(`/api/tasks/${taskId}`, { method: 'PUT', body: JSON.stringify(payload) });
      showMessage('Task updated.', true);
      await refreshDashboard();
    } catch (error) {
      console.error('Update failed:', error);
      showMessage(error.message);
    }
  }

  async function deleteTask(taskId) {
    try {
      await apiRequest(`/api/tasks/${taskId}`, { method: 'DELETE' });
      showMessage('Task deleted.', true);
      await refreshDashboard();
    } catch (error) {
      console.error('Delete failed:', error);
      showMessage(error.message);
    }
  }

  const form = document.getElementById('task-form');
  form.addEventListener('submit', async (event) => {
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
      document.getElementById('title').focus();
      return;
    }

    const submitButton = form.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    try {
      await apiRequest('/api/tasks', { method: 'POST', body: JSON.stringify(data) });
      form.reset();
      showMessage('Task created.', true);
      await refreshDashboard();
    } catch (error) {
      console.error('Create task failed:', error);
      showMessage(error.message);
    } finally {
      submitButton.disabled = false;
    }
  });

  seedButton.addEventListener('click', async () => {
    seedButton.disabled = true;
    try {
      const response = await apiRequest('/api/seed', { method: 'POST' });
      const result = await response.json();
      showMessage(`${result.added} sample tasks added to your board.`, true);
      await refreshDashboard();
    } catch (error) {
      console.error('Could not add sample tasks:', error);
      showMessage(error.message);
    } finally {
      seedButton.disabled = false;
    }
  });

  [categoryFilter, statusFilter].forEach((filter) => {
    filter.addEventListener('change', () => {
      loadTasks().catch((error) => {
        console.error('Failed to filter tasks:', error);
        showMessage(error.message);
      });
    });
  });

  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      loadTasks().catch((error) => {
        console.error('Failed to search tasks:', error);
        showMessage(error.message);
      });
    }, 180);
  });

  refreshDashboard();
});
