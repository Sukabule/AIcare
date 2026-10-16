function confirmAction(message, acceptLabel = '确认运行') {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'action-confirm';
    dialog.setAttribute('aria-label', '确认作业');
    const title = document.createElement('h2'); title.textContent = '确认操作';
    const copy = document.createElement('p'); copy.textContent = message;
    const actions = document.createElement('div'); actions.className = 'actions';
    const cancel = document.createElement('button'); cancel.textContent = '取消';
    const accept = document.createElement('button'); accept.textContent = acceptLabel; accept.className = 'primary';
    const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
    cancel.onclick = () => finish(false); accept.onclick = () => finish(true);
    dialog.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
    actions.append(cancel, accept); dialog.append(title, copy, actions);
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  });
}
