(() => {
  const data = document.getElementById("calendar-export-data");
  if (!data) return;
  let view = JSON.parse(data.textContent);
  const csrf = view.csrf, base = "/calendar/sessions/" + encodeURIComponent(view.session_id);
  const status = document.getElementById("calendar-export-status");
  const dialog = document.getElementById("calendar-export-dialog");
  const add = document.getElementById("calendar-add-confirm");
  const remove = document.getElementById("calendar-remove-confirm");
  const permission = document.getElementById("calendar-permission");
  function closePreview() {dialog.hidden=true;document.getElementById("calendar-review").focus();}
  document.getElementById("calendar-export-close").onclick=closePreview;
  document.addEventListener("keydown",event=>{
    if(dialog.hidden) return;
    if(event.key==="Escape") {event.preventDefault();closePreview();}
    if(event.key==="Tab") {
      const items=Array.from(dialog.querySelectorAll("button")).filter(e=>!e.disabled&&e.getClientRects().length);
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first) {event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last) {event.preventDefault();first.focus();}
    }
  });
  const names = {ready:"Ready to review and confirm.", added:"Added to your calendar.",
    failed:"The previous attempt failed. Review the current details before retrying.",
    BUSY_CONFLICT:"A schedule conflict prevents this export. Check the session time.",
    PREVIEW_CHANGED:"The session or account changed. Review the current version again.",
    CURRENT_EXPORT_APPROVAL_REQUIRED:"Review and confirm the current session before exporting.",
    REMOTE_EVENT_CHANGED:"The linked Google event changed. Inspect it before continuing.",
    GOOGLE_ACCOUNT_BINDING_CHANGED:"This export belongs to the previously confirmed Google account. Reconnect that account before changing it.",
    adding:"Adding — check the result before retrying.", removing:"Removing — check the result before retrying.",
    removed:"Removed from your calendar. The invitation is unchanged.",
    unknown:"Result pending verification. Review before retrying.",
    WRITE_SCOPE_REQUIRED:"Additional calendar write permission is required.",
    GOOGLE_CONNECTION_REQUIRED:"Connect your Google account first.",
    WAITING_FOR_BOTH_ACCEPTANCES:"Waiting for both participants to accept.",
    SESSION_CHANGED_NEW_INVITATION_REQUIRED:"Session changed. Use a new invitation; the old event will not be modified.",
    GROUP_EXPORT_POLICY_PENDING:"Group calendar export is unavailable until the acceptance policy is agreed."};
  function show() {
    status.textContent = names[view.status] || "Calendar action unavailable. Review the current session and permissions.";
    permission.hidden = view.status !== "WRITE_SCOPE_REQUIRED";
    const link = document.getElementById("calendar-result-link");
    link.hidden = !view.html_link;
    if (view.html_link) {
      const url = new URL(view.html_link);
      if (url.protocol === "https:" && ["www.google.com","calendar.google.com"].includes(url.hostname)) link.href = url.href;
      else link.hidden = true;
    }
  }
  async function call(path, method="GET", body) {
    const response = await fetch(base+path,{method,credentials:"same-origin",
      headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
      body:body ? JSON.stringify(body) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "PROVIDER_RESULT_UNKNOWN");
    return result;
  }
  document.getElementById("calendar-review").onclick = async () => {
    try {
      view = {...view,...await call("/preview")}; show();
      document.getElementById("calendar-action-summary").textContent =
        view.course+" · "+view.starts_at+" – "+view.ends_at+" · "+view.timezone+" · "+view.place+" · "+view.target;
      add.hidden = view.intent==="delete" || !["ready","unknown","failed","adding"].includes(view.status);
      remove.hidden = !["added","unknown","failed","SESSION_CHANGED_NEW_INVITATION_REQUIRED"].includes(view.status) || !view.delete_fingerprint;
      dialog.hidden=false;document.getElementById("calendar-export-close").focus();
    } catch (error) { status.textContent = names[error.message] || "Unable to review calendar action."; }
  };
  async function execute(kind) {
    add.disabled = remove.disabled = true;
    try {
      const approval = await call("/approval","POST",{
        fingerprint:kind==="export" ? view.fingerprint : view.delete_fingerprint,kind});
      view = {...view,...await call("/export",kind==="export" ? "POST" : "DELETE",approval)};
      show(); closePreview();
    } catch (error) {
      view.status = error.message === "PROVIDER_RESULT_UNKNOWN" ? "unknown" : error.message;
      show(); closePreview();
    } finally { add.disabled = remove.disabled = false; }
  }
  add.onclick = () => execute("export");
  remove.onclick = () => execute("delete");
  permission.onclick = async () => {
    try {
      const result = await call("/write-permission","POST");
      const target = new URL(result.authorization_url);
      if (target.origin!=="https://accounts.google.com") throw new Error("invalid target");
      location.assign(target.href);
    } catch (_) { status.textContent = "Calendar authorization could not be started."; }
  };
  show();
})();
