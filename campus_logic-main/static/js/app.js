document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  if (csrfToken && document.body.dataset.presenceEnabled === "true") {
    let lastActivity = Date.now();
    const markActive = () => { lastActivity = Date.now(); };
    ["pointerdown", "pointermove", "keydown", "touchstart", "scroll"].forEach((eventName) => {
      document.addEventListener(eventName, markActive, { passive: true });
    });
    const updatePresence = async () => {
      try {
        const state = !document.hidden && Date.now() - lastActivity < 120000 ? "online" : "away";
        const ownPresence = document.querySelector("[data-user-presence]");
        if (ownPresence) {
          ownPresence.dataset.presence = state;
          ownPresence.title = state === "online" ? "You are online" : "You are away";
        }
        const response = await fetch("/communication/presence", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify({ state }),
          cache: "no-store",
        });
        if (!response.ok) console.error("Unable to update online status:", response.status);
      } catch (error) {
        console.error("Unable to update online status:", error);
      }
    };
    updatePresence();
    window.setInterval(updatePresence, 30000);
    document.addEventListener("visibilitychange", updatePresence);
  }

  document.querySelectorAll("form[method='post'], form[method='POST']").forEach((form) => {
    if (csrfToken && !form.querySelector("input[name='csrf_token']")) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.appendChild(input);
    }
  });

  const menuToggle = document.querySelector("[data-menu-toggle]");
  const sidebar = document.getElementById("sidebar");
  if (menuToggle && sidebar) {
    menuToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
  }

  document.querySelectorAll("[data-dismiss]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".flash")?.remove());
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-table-search]").forEach((input) => {
    input.addEventListener("input", () => {
      const panel = input.closest(".panel");
      const table = panel?.querySelector("[data-filter-table]");
      if (!table) return;
      const term = input.value.trim().toLocaleLowerCase();
      table.querySelectorAll("tbody tr").forEach((row) => {
        row.hidden = !row.textContent.toLocaleLowerCase().includes(term);
      });
    });
  });

  document.querySelectorAll("[data-contact-search]").forEach((input) => {
    input.addEventListener("input", () => {
      const term = input.value.trim().toLocaleLowerCase();
      document.querySelectorAll("[data-contact-card]").forEach((card) => {
        card.hidden = !card.textContent.toLocaleLowerCase().includes(term);
      });
    });
  });

  document.querySelectorAll("[data-fill-query]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById("quick-query");
      if (input) {
        input.value = button.dataset.fillQuery;
        input.focus();
      }
    });
  });

  const queryType = document.getElementById("query_type");
  if (queryType) {
    const valueField = document.querySelector("[data-builder-value]");
    const personField = document.querySelector("[data-builder-person]");
    const departmentField = document.querySelector("[data-builder-department]");
    const valueLabel = document.querySelector("[data-value-label]");
    const valueInput = document.getElementById("value");
    const departmentQueries = new Set([
      "professors_in_department",
      "courses_offered",
      "professors_teaching_department",
      "hod_of_department",
      "students_in_department_subjects",
    ]);
    const courseQueries = new Set(["who_teaches", "students_enrolled", "students_study"]);
    const noValueQueries = new Set(["low_attendance", "pending_fees", "pending_submissions"]);
    const updateBuilder = () => {
      const type = queryType.value;
      const associated = type === "is_associated";
      valueField.hidden = associated || noValueQueries.has(type);
      personField.hidden = !associated;
      departmentField.hidden = !associated;
      if (type === "department_of_professor") {
        valueLabel.textContent = "Professor";
        valueInput.setAttribute("list", "people-values");
        valueInput.placeholder = "e.g. Meena";
      } else if (departmentQueries.has(type)) {
        valueLabel.textContent = "Department";
        valueInput.setAttribute("list", "department-values");
        valueInput.placeholder = "e.g. CSE";
      } else {
        valueLabel.textContent = "Course";
        valueInput.setAttribute("list", "course-values");
        valueInput.placeholder = "e.g. AI";
      }
      if (courseQueries.has(type)) valueInput.setAttribute("list", "course-values");
    };
    queryType.addEventListener("change", updateBuilder);
    updateBuilder();
  }

  const predicate = document.getElementById("predicate");
  if (predicate) {
    const secondArgument = document.querySelector("[data-fact-arg2]");
    const secondInput = document.getElementById("arg2");
    const unary = new Set(["Student", "Professor", "Course", "Department"]);
    const updateFactForm = () => {
      const isUnary = unary.has(predicate.value);
      secondArgument.hidden = isUnary;
      secondInput.required = !isUnary;
      secondInput.disabled = isUnary;
    };
    predicate.addEventListener("change", updateFactForm);
    updateFactForm();
  }

  const userRole = document.getElementById("role");
  if (userRole) {
    const student = document.querySelector("[data-user-student]");
    const professor = document.querySelector("[data-user-professor]");
    const department = document.querySelector("[data-user-department]");
    const studentSelect = student.querySelector("select");
    const professorSelect = professor.querySelector("select");
    const departmentSelect = department.querySelector("select");
    const updateRoleFields = () => {
      student.hidden = false;
      professor.hidden = userRole.value !== "faculty";
      department.hidden = userRole.value !== "hod";
      studentSelect.disabled = student.hidden;
      professorSelect.disabled = professor.hidden;
      departmentSelect.disabled = department.hidden;
      studentSelect.required = !student.hidden;
      professorSelect.required = !professor.hidden;
      departmentSelect.required = !department.hidden;
      if (student.hidden) studentSelect.value = "";
      if (professor.hidden) professorSelect.value = "";
      if (department.hidden) departmentSelect.value = "";
    };
    userRole.addEventListener("change", updateRoleFields);
    updateRoleFields();
  }
});
