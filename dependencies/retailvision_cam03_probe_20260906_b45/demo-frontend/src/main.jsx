import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const isCustomerDisplay = window.location.pathname === "/customer-display"
  || window.location.pathname.startsWith("/customer-display/");
const CustomerDisplayPage = lazy(() => import("./features/customer-display/CustomerDisplayPage")
  .then((module) => ({ default: module.CustomerDisplayPage })));

createRoot(document.getElementById("root")).render(
  <StrictMode>
    {isCustomerDisplay ? <Suspense fallback={null}><CustomerDisplayPage /></Suspense> : <App />}
  </StrictMode>,
);
