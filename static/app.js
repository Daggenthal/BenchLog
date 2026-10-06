(function () {
  "use strict";

  // Server and browser clocks can differ, so measure the offset once.
  var serverNow = parseInt(document.body.getAttribute("data-now"), 10) || Math.floor(Date.now() / 1000);
  var offset = Math.floor(Date.now() / 1000) - serverNow;

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function clock(total) {
    total = Math.max(0, Math.floor(total));
    var h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return (h ? h + ":" + pad(m) : m) + ":" + pad(s);
  }

  // Live clocks: data-base is seconds already banked, data-since is when the running session began.
  var clocks = document.querySelectorAll("[data-clock]");
  function tick() {
    var nowServer = Math.floor(Date.now() / 1000) - offset;
    for (var i = 0; i < clocks.length; i++) {
      var el = clocks[i];
      var base = parseInt(el.getAttribute("data-base"), 10) || 0;
      var since = parseInt(el.getAttribute("data-since"), 10);
      el.textContent = clock(since ? base + (nowServer - since) : base);
    }
  }
  if (clocks.length) { tick(); setInterval(tick, 1000); }

  // Checklist: save each tap straight away so nothing is lost if the phone locks.
  var checklist = document.getElementById("checklist-form");
  if (checklist && window.fetch) {
    var status = document.getElementById("checklist-status");
    checklist.addEventListener("change", function (ev) {
      if (!ev.target || ev.target.type !== "radio") { return; }
      var data = new FormData();
      data.append(ev.target.name, ev.target.value);
      if (status) { status.textContent = "Saving..."; }
      fetch(checklist.action, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (status) {
            status.textContent = "Saved. " + j.working + " working, " + j.failed + " failed, " +
              (j.total - j.working - j.failed) + " untested.";
          }
        })
        .catch(function () { if (status) { status.textContent = "Could not save. Use the Save button."; } });
    });
  }

  // Repair form: show the donor field only when needed, and prefill the usual part cost.
  var repair = document.getElementById("repair-form");
  if (repair) {
    var source = repair.querySelector("[name=source]");
    var part = repair.querySelector("[name=part_id]");
    var cost = repair.querySelector("[name=cost]");
    var donorField = document.getElementById("donor-field");
    var touched = false;
    cost.addEventListener("input", function () { touched = true; });
    function sync() {
      var isDonor = source.value === "donor";
      donorField.style.display = isDonor ? "" : "none";
      if (touched) { return; }
      var option = part.options[part.selectedIndex];
      if (source.value === "purchased" && option && option.getAttribute("data-cost")) {
        cost.value = option.getAttribute("data-cost");
      } else {
        cost.value = source.value === "purchased" ? "" : "0";
      }
    }
    source.addEventListener("change", sync);
    part.addEventListener("change", sync);
    sync();
  }

  // Tapping a donor suggestion fills in the donor ID.
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest ? ev.target.closest("[data-fill-donor]") : null;
    if (!el || !repair) { return; }
    ev.preventDefault();
    repair.querySelector("[name=source]").value = "donor";
    repair.querySelector("[name=donor_code]").value = el.getAttribute("data-fill-donor");
    var wanted = el.getAttribute("data-part");
    var select = repair.querySelector("[name=part_id]");
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].getAttribute("data-name") === wanted) { select.selectedIndex = i; break; }
    }
    select.dispatchEvent(new Event("change"));
    repair.scrollIntoView({ behavior: "smooth", block: "center" });
  });
})();

(function () {
  "use strict";

  // System page: refresh the health panel without reloading the forms around it.
  var health = document.getElementById("health-body");
  if (health && window.fetch) {
    setInterval(function () {
      if (document.hidden) { return; }
      fetch(health.getAttribute("data-refresh"))
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html) { health.innerHTML = html; } })
        .catch(function () {});
    }, 10000);
  }

  // Job page: follow the log until the task ends. Keeps trying while the app restarts.
  var job = document.getElementById("job-status");
  if (job && window.fetch && job.getAttribute("data-status") === "running") {
    var logBox = document.getElementById("job-log");
    var appStarted = parseInt(job.getAttribute("data-started"), 10);
    var timer = setInterval(function () {
      fetch(job.getAttribute("data-url"))
        .then(function (r) { if (!r.ok) { throw new Error("gone"); } return r.json(); })
        .then(function (j) {
          var atBottom = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 30;
          logBox.textContent = j.log;
          if (atBottom) { logBox.scrollTop = logBox.scrollHeight; }
          if (j.status === "running") { return; }
          clearInterval(timer);
          if (j.status === "done") {
            job.textContent = j.restarts ? "Finished. The app is restarting, then this page returns to System." : "Finished.";
            if (j.restarts) { waitForRestart(job.getAttribute("data-back"), appStarted); }
          } else if (j.status === "failed") {
            job.textContent = "Failed. The log below shows where it stopped.";
          } else {
            job.textContent = "The app restarted while this was running.";
          }
        })
        .catch(function () { job.textContent = "Waiting for the app to come back..."; });
    }, 2000);
  }

  // Wait until the app answers again with a new start time, then go back to System.
  function waitForRestart(back, startedBefore) {
    var seenDown = false, tries = 0;
    var poll = setInterval(function () {
      tries += 1;
      fetch("/system/ping", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var restarted = startedBefore ? j.started !== startedBefore : seenDown;
          if (restarted || tries > 150) { clearInterval(poll); window.location = back; }
        })
        .catch(function () { seenDown = true; });
    }, 2000);
  }

  var reboot = document.getElementById("reboot-status");
  if (reboot && window.fetch) {
    waitForRestart(reboot.getAttribute("data-back"), parseInt(reboot.getAttribute("data-started"), 10));
  }
})();
