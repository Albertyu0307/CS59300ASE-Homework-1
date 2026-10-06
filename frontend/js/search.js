// Logic for index.html: search form and results list.

import { getHealth } from "./api.js";

const healthEl = document.getElementById("health");

getHealth()
  .then((data) => { healthEl.textContent = `Backend: ${data.status}`; })
  .catch((err) => { healthEl.textContent = `Backend unreachable: ${err.message}`; });
