const checkKnowledge = document.querySelector('#knowledge-status-check');
const knowledgeResult = document.querySelector('#knowledge-status-result');
checkKnowledge.addEventListener('click', async () => {
  checkKnowledge.disabled = true;
  try {
    const raw = await cockpit.spawn(['/usr/bin/python3', '-B', '/opt/jp-reply-capture/knowledge_status.py'], {superuser:'require',err:'message'});
    const report = JSON.parse(raw);
    const lines = ['Revisão ' + report.revision];
    for (const topic of report.topics) {
      lines.push(topic.title + ' — ' + topic.state);
      for (const [name, count] of Object.entries(topic.counts)) lines.push('  ' + name + ' · ' + count);
    }
    knowledgeResult.textContent = lines.join('\n');
  } catch {
    knowledgeResult.textContent = 'Consulta indisponível. Confira sua sessão administrativa no Dell.';
  } finally {
    checkKnowledge.disabled = false;
  }
});
