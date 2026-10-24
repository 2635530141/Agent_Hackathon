(() => {
  const initial = JSON.parse(document.getElementById("calendar-week-data").textContent);
  const week = initial.week, grid = document.getElementById("calendar-grid");
  const dialog = document.getElementById("calendar-editor"), form = document.getElementById("calendar-entry-form");
  const error = document.getElementById("calendar-editor-error");
  const remove = document.getElementById("calendar-entry-remove");
  const deleteConfirm = document.getElementById("calendar-delete-confirmation");
  const save = document.getElementById("calendar-entry-save");
  const hoursSelect = document.getElementById("calendar-hours-range");
  const outsideButton = document.getElementById("calendar-outside-times");
  const renderedEntries = [];
  let rangeStart=16, rangeEnd=46; // Half-hour units: 08:00–23:00.
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
    renderedEntries.push({element,entry,start:entry.top*48/100,end:(entry.top+entry.height)*48/100});
  });
  function updateOutsideNotice() {
    const dayView=document.getElementById("calendar-view").value==="day";
    const count=renderedEntries.filter(({entry,start,end})=>entry.kind!=="available" &&
      (!dayView || entry.day===activeDay) && (start<rangeStart-0.001 || end>rangeEnd+0.001)).length;
    outsideButton.hidden=!count;
    outsideButton.textContent=count+" outside view · Show all";
    outsideButton.setAttribute("aria-label","Show all hours: "+count+" "+(count===1?"block extends":"blocks extend")+" outside the visible hours");
  }
  function renderHours() {
    rangeStart=hoursSelect.value==="all"?0:16;
    rangeEnd=hoursSelect.value==="all"?48:46;
    const span=rangeEnd-rangeStart;
    grid.style.setProperty("--calendar-visible-hours",span/2);
    grid.querySelectorAll(".calendar-hours [data-hour]").forEach(label=>{
      const half=Number(label.dataset.hour)*2;
      label.hidden=half<rangeStart || half>rangeEnd;
      label.style.top=(half-rangeStart)/span*100+"%";
      label.classList.toggle("range-first",half===rangeStart);
      label.classList.toggle("range-last",half===rangeEnd);
    });
    grid.querySelectorAll(".cal-slot").forEach(slot=>{
      const half=Number(slot.dataset.half);
      slot.title=slot.getAttribute("aria-label");
      slot.hidden=half<rangeStart || half>=rangeEnd;
      slot.style.top=(half-rangeStart)/span*100+"%";
      slot.style.height=100/span+"%";
    });
    renderedEntries.forEach(({element,start,end})=>{
      const a=Math.max(start,rangeStart),b=Math.min(end,rangeEnd);
      element.hidden=b<=a;
      element.style.top=(a-rangeStart)/span*100+"%";
      element.style.height=Math.max(0,b-a)/span*100+"%";
    });
    updateOutsideNotice();
    fitTimetable();
  }
  function viewChanged() {
    const dayView=document.getElementById("calendar-view").value==="day";
    document.querySelector(".calendar-board").classList.toggle("calendar-day-view",dayView);
    grid.querySelectorAll("[data-day]").forEach(el=>el.classList.toggle("active-day",Number(el.dataset.day)===activeDay));
    updateOutsideNotice();
  }
  const viewSelect=document.getElementById("calendar-view");
  // Start with the complete week, including on narrow screens.
  const viewKey="studymate-calendar-full-view";
  try {viewSelect.value=sessionStorage.getItem(viewKey) || "week";} catch (_) {}
  viewSelect.onchange=()=>{try{sessionStorage.setItem(viewKey,viewSelect.value);}catch(_){}viewChanged();}; viewChanged();
  grid.querySelectorAll(".calendar-day-heading").forEach(button=>button.onclick=()=>{
    activeDay=Number(button.dataset.day); viewSelect.value="day";
    try{sessionStorage.setItem(viewKey,"day");}catch(_){}
    location.assign("/schedule?day="+week.days[activeDay].date);
  });
  function fitTimetable() {
    const column=grid.querySelector(".calendar-day.active-day");
    const footer=document.getElementById("calendar-selection-status");
    // Measure real controls, including wrapped Google status or validation messages.
    const top=column.getBoundingClientRect().top+window.scrollY;
    const height=Math.max(192,window.innerHeight-top-footer.getBoundingClientRect().height-18);
    grid.style.setProperty("--calendar-day-height",height+"px");
    const hourStep=height/((rangeEnd-rangeStart)/2)<32?2:1;
    grid.style.setProperty("--calendar-hour-step",hourStep);
    grid.querySelectorAll(".calendar-hours [data-hour]").forEach(label=>{
      const hour=Number(label.dataset.hour),half=hour*2;
      label.hidden=half<rangeStart || half>rangeEnd ||
        (hour%hourStep!==0 && half!==rangeStart && half!==rangeEnd);
    });
    document.getElementById("calendar-scroller").setAttribute("aria-label","Timetable "+clock(rangeStart)+"–"+(rangeEnd===48?"24:00":clock(rangeEnd)));
    renderedEntries.forEach(({element})=>element.classList.toggle("cal-event-short",element.getBoundingClientRect().height<32));
  }
  const resize=new ResizeObserver(fitTimetable);
  document.querySelectorAll(".site-header,.calendar-connection,.calendar-toolbar,.calendar-meta,.calendar-zone,.calendar-page>.callout")
    .forEach(element=>resize.observe(element));
  window.addEventListener("resize",fitTimetable);
  try {hoursSelect.value=sessionStorage.getItem("studymate-calendar-visible-hours") || "study";} catch (_) {}
  hoursSelect.onchange=()=>{try{sessionStorage.setItem("studymate-calendar-visible-hours",hoursSelect.value);}catch(_){}renderHours();};
  outsideButton.onclick=()=>{hoursSelect.value="all";hoursSelect.onchange();hoursSelect.focus();};
  renderHours();
  function selectedSlot(target) { return target.closest && target.closest(".cal-slot"); }
  grid.addEventListener("pointerdown",event=>{
    const slot=selectedSlot(event.target);
    if (!slot || event.button!==0 || event.pointerType==="touch") return;
    drag={column:slot.parentElement,start:Number(slot.dataset.half),end:Number(slot.dataset.half)};
    selection=document.createElement("div"); selection.className="cal-selection"; drag.column.append(selection);
    selection.style.top=(drag.start-rangeStart)/(rangeEnd-rangeStart)*100+"%";
    selection.style.height=100/(rangeEnd-rangeStart)+"%"; event.preventDefault();
  });
  grid.addEventListener("pointermove",event=>{
    if (!drag) return;
    const rect=drag.column.getBoundingClientRect();
    const span=rangeEnd-rangeStart;
    drag.end=Math.max(rangeStart,Math.min(rangeEnd-1,rangeStart+Math.floor((event.clientY-rect.top)/rect.height*span)));
    selection.style.top=(Math.min(drag.start,drag.end)-rangeStart)/span*100+"%";
    selection.style.height=(Math.abs(drag.end-drag.start)+1)/span*100+"%";
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
  field("kind").onchange=kindChanged;
  remove.onclick=()=>{deleteConfirm.hidden=false;};
})();
