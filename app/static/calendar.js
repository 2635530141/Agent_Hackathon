(() => {
  const initial = JSON.parse(document.getElementById("calendar-week-data").textContent);
  const week = initial.week, grid = document.getElementById("calendar-grid");
  const scroller = document.getElementById("calendar-scroller");
  const dialog = document.getElementById("calendar-editor"), form = document.getElementById("calendar-entry-form");
  const error = document.getElementById("calendar-editor-error");
  const remove = document.getElementById("calendar-entry-remove");
  const deleteConfirm = document.getElementById("calendar-delete-confirmation");
  const save = document.getElementById("calendar-entry-save");
  let editing=null, activeDay=Math.max(0,week.days.findIndex(d=>d.selected)), drag=null, selection=null;
  let returnFocus=null;
  function closeEditor() {dialog.hidden=true;if(returnFocus) returnFocus.focus();}
  const field = name => form.elements.namedItem(name);
  const clock = half => String(Math.floor(half/2)).padStart(2,"0")+":"+(half%2 ? "30":"00");
  function datetime(day,half) {
    if (half===48) {
      const next = new Date(day+"T12:00:00Z"); next.setUTCDate(next.getUTCDate()+1);
      return next.toISOString().slice(0,10)+"T00:00";
    }
    return day+"T"+clock(half);
  }
  function kindChanged() {
    const kind=field("kind").value;
    field("entry_kind").value=kind;
    document.getElementById("calendar-course-field").hidden=kind!=="exam";
    document.getElementById("calendar-priority-field").hidden=kind!=="exam";
    document.getElementById("calendar-entry-help").textContent=kind==="weekly"
      ? "Repeats on this weekday. Saving replaces this weekday’s study hours. Other weekdays stay unchanged."
      : kind==="exam" ? "Exams always block time. Optional priority is ranking information only."
      : kind==="busy" ? "This interval is unavailable, including inside your study hours."
      : "Extra study time applies only to these dates. Exams and busy events still block it.";
  }
  function openEditor(entry) {
    editing=entry.id ? entry : null;
    const readonly=entry.editable===false;
    form.reset(); error.hidden=true; deleteConfirm.hidden=true;
    document.getElementById("calendar-editor-title").textContent=readonly ? entry.label : editing ? "Edit time" : "Create time";
    field("kind").value=readonly ? "busy" : entry.kind || "free";
    field("starts_at").value=entry.starts_at; field("ends_at").value=entry.ends_at;
    field("record_id").value=entry.id || "";
    field("priority_starts_at").value=entry.priority_starts_at || "";
    if (entry.course_id) field("course_id").value=entry.course_id;
    for (const input of form.querySelectorAll("input,select")) input.disabled=readonly;
    field("kind").disabled=readonly || Boolean(editing); save.hidden=readonly; remove.hidden=readonly || !editing;
    kindChanged();
    if (readonly) document.getElementById("calendar-entry-help").textContent="Read-only calendar or confirmed session. Manage the source event in its original app.";
    returnFocus=document.activeElement;dialog.hidden=false;
    document.getElementById("calendar-editor-close").focus();
  }
  week.entries.forEach(entry => {
    const column=grid.querySelector('.calendar-day[data-day="'+entry.day+'"]');
    let element;
    if (entry.kind==="weekly") {
      element=document.createElement("div"); element.className="cal-kind-weekly";
      const button=document.createElement("button"); button.type="button"; button.textContent="Study hours";
      button.setAttribute("aria-label","Edit study hours "+week.days[entry.day].date);
      button.onclick=()=>openEditor(entry); element.append(button);
    } else if (entry.kind==="available") {
      element=document.createElement("div"); element.className="cal-kind-available";
    } else {
      element=document.createElement("button"); element.type="button";
      element.className="cal-event cal-kind-"+entry.kind;
      element.style.left="calc("+entry.left+"% + 3px)"; element.style.width="calc("+entry.width+"% - 6px)";
      const label=document.createElement("strong"); label.textContent=entry.label;
      const when=document.createElement("small"); when.textContent=entry.time_label;
      element.append(label,when); element.onclick=()=>openEditor(entry);
      element.setAttribute("aria-label",entry.label+" "+week.days[entry.day].date+" "+entry.time_label);
    }
    element.dataset.layer=entry.kind; element.style.top=entry.top+"%"; element.style.height=entry.height+"%";
    element.title=entry.label+" · "+entry.time_label+" · "+week.timezone; column.append(element);
  });
  function viewChanged() {
    const dayView=document.getElementById("calendar-view").value==="day";
    document.querySelector(".calendar-board").classList.toggle("calendar-day-view",dayView);
    grid.querySelectorAll("[data-day]").forEach(el=>el.classList.toggle("active-day",Number(el.dataset.day)===activeDay));
  }
  const viewSelect=document.getElementById("calendar-view");
  try {viewSelect.value=sessionStorage.getItem("studymate-calendar-view") || (matchMedia("(max-width:700px)").matches?"day":"week");}
  catch (_) {if (matchMedia("(max-width:700px)").matches) viewSelect.value="day";}
  viewSelect.onchange=()=>{try{sessionStorage.setItem("studymate-calendar-view",viewSelect.value);}catch(_){}viewChanged();}; viewChanged();
  grid.querySelectorAll(".calendar-day-heading").forEach(button=>button.onclick=()=>{
    activeDay=Number(button.dataset.day); viewSelect.value="day";
    try{sessionStorage.setItem("studymate-calendar-view","day");}catch(_){}
    location.assign("/schedule?day="+week.days[activeDay].date);
  });
  scroller.scrollTop=8*60;
  document.querySelectorAll("[data-layer]").forEach(input=>{
    if (input.tagName!=="INPUT") return;
    input.onchange=()=>grid.querySelectorAll('[data-layer="'+input.dataset.layer+'"]').forEach(el=>el.hidden=!input.checked);
  });
  function selectedSlot(target) { return target.closest && target.closest(".cal-slot"); }
  grid.addEventListener("pointerdown",event=>{
    const slot=selectedSlot(event.target);
    if (!slot || event.button!==0 || event.pointerType==="touch") return;
    drag={column:slot.parentElement,start:Number(slot.dataset.half),end:Number(slot.dataset.half)};
    selection=document.createElement("div"); selection.className="cal-selection"; drag.column.append(selection);
    selection.style.top=drag.start*30+"px"; selection.style.height="30px"; event.preventDefault();
  });
  grid.addEventListener("pointermove",event=>{
    if (!drag) return;
    const rect=drag.column.getBoundingClientRect();
    drag.end=Math.max(0,Math.min(47,Math.floor((event.clientY-rect.top)/30)));
    selection.style.top=Math.min(drag.start,drag.end)*30+"px";
    selection.style.height=(Math.abs(drag.end-drag.start)+1)*30+"px";
  });
  document.addEventListener("pointerup",()=>{
    if (!drag) return;
    const day=drag.column.dataset.date, a=Math.min(drag.start,drag.end),b=Math.max(drag.start,drag.end)+1;
    selection.remove(); drag=null;
    document.getElementById("calendar-selection-status").textContent="Selected "+day+" "+clock(a)+"–"+(b===48?"24:00":clock(b));
    openEditor({kind:"free",starts_at:datetime(day,a),ends_at:datetime(day,b)});
  });
  document.addEventListener("pointercancel",()=>{if(selection) selection.remove();drag=null;});
  grid.querySelectorAll(".cal-slot").forEach(slot=>slot.onclick=event=>{
    if (event.detail===0 || event.pointerType==="touch") {
      const half=Number(slot.dataset.half),day=slot.parentElement.dataset.date;
      openEditor({kind:"free",starts_at:datetime(day,half),ends_at:datetime(day,half+1)});
    }
  });
  document.getElementById("calendar-create").onclick=()=>openEditor({kind:"free",
    starts_at:week.days[activeDay].date+"T13:00",ends_at:week.days[activeDay].date+"T14:00"});
  document.getElementById("calendar-editor-close").onclick=closeEditor;
  document.addEventListener("keydown",event=>{
    if(dialog.hidden) return;
    if(event.key==="Escape") {event.preventDefault();closeEditor();}
    if(event.key==="Tab") {
      const items=Array.from(dialog.querySelectorAll("button,input,select")).filter(e=>!e.disabled&&e.getClientRects().length);
      if(!items.length) return;
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first) {event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last) {event.preventDefault();first.focus();}
    }
  });
  document.getElementById("calendar-jump").onchange=event=>{if(event.target.value) location.assign("/schedule?day="+event.target.value);};
  field("kind").onchange=kindChanged;
  remove.onclick=()=>{deleteConfirm.hidden=false;};
})();
