import React, { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import App from "./AppV2";
import AuthGate from "./auth/AuthGate.jsx";
import "./styles.css";
import "./enhancements.css";

const TripTestApp = lazy(() => import("./trip/TripTestApp"));
const isTripTest = new URLSearchParams(window.location.search).get("module") === "trip";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthGate>
      {(session) =>
        isTripTest ? (
          <Suspense fallback={<div className="app-loading">正在加载行程测试模块...</div>}>
            <TripTestApp user={session.user} />
          </Suspense>
        ) : (
          <App user={session.user} authMode={session.authMode} />
        )
      }
    </AuthGate>
  </React.StrictMode>,
);
