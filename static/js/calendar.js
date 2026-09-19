// PCT CRM — week calendar drag-and-drop rescheduling (no external deps)

document.addEventListener('DOMContentLoaded', () => {
  const chips = document.querySelectorAll('.visit-chip[draggable="true"]');
  const zones = document.querySelectorAll('.dropzone');
  if (!chips.length || !zones.length) return;

  let draggedId = null;

  chips.forEach(chip => {
    chip.addEventListener('dragstart', (e) => {
      draggedId = chip.dataset.visitId;
      e.dataTransfer.effectAllowed = 'move';
      setTimeout(() => chip.style.opacity = '0.35', 0);
    });
    chip.addEventListener('dragend', () => {
      chip.style.opacity = '1';
    });
    // Prevent the calendar click-through when the user was just dragging
    chip.addEventListener('click', (e) => {
      if (chip.dataset.justDragged === '1') {
        e.preventDefault();
        chip.dataset.justDragged = '0';
      }
    });
  });

  zones.forEach(zone => {
    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      if (!draggedId) return;
      const newDate = zone.dataset.date;

      fetch('/scheduling/visits/' + draggedId + '/quick-move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({ scheduled_date: newDate }),
      })
        .then(r => r.json().then(data => ({ status: r.status, data })))
        .then(({ status, data }) => {
          if (status === 200 && data.ok) {
            window.location.reload();
          } else {
            showToast(data.error || 'Could not reschedule that visit.', 'error');
          }
        })
        .catch(() => showToast('Network error while rescheduling.', 'error'));
    });
  });
});
