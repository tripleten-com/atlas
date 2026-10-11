import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Spinner, ToastProvider } from "./components";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { FacilityProvider } from "./context/FacilityContext";
import { LiveProvider } from "./context/LiveContext";
import AppShell from "./layout/AppShell";
import Activity from "./pages/Activity";
import AiFeatures from "./pages/AiFeatures";
import Dashboard from "./pages/Dashboard";
import Encounters from "./pages/Encounters";
import IntegrationDetail from "./pages/IntegrationDetail";
import Integrations from "./pages/Integrations";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import PatientDetail from "./pages/PatientDetail";
import Patients from "./pages/Patients";
import Results from "./pages/Results";
import Settings from "./pages/Settings";
import Usage from "./pages/Usage";

function RequireAuth({ children }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Checking your session" size={28} />
      </div>
    );
  }
  if (status !== "signed-in") return <Navigate to="/login" replace state={{ from: location }} />;
  return children;
}

function RequireAdmin({ children }) {
  const { isAdmin } = useAuth();
  return isAdmin ? children : <Navigate to="/dashboard" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <FacilityProvider>
                  <LiveProvider>
                    <AppShell />
                  </LiveProvider>
                </FacilityProvider>
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/patients" element={<Patients />} />
            <Route path="/patients/:patientId" element={<PatientDetail />} />
            <Route path="/encounters" element={<Encounters />} />
            <Route path="/results" element={<Results />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/integrations/:connectionId" element={<IntegrationDetail />} />
            <Route path="/ai" element={<AiFeatures />} />
            <Route
              path="/usage"
              element={
                <RequireAdmin>
                  <Usage />
                </RequireAdmin>
              }
            />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
