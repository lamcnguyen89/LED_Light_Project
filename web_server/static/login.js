"use strict";

const loginDialog = document.querySelector("#login-dialog");
if (typeof loginDialog.showModal === "function") {
  loginDialog.removeAttribute("open");
  loginDialog.showModal();
  loginDialog.addEventListener("cancel", (event) => event.preventDefault());
}
