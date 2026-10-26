import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import { OwnerBar } from "./components/OwnerBar";
import { LoginPage } from "./pages/LoginPage";
import { NbaReportPage } from "./pages/NbaReportPage";
import { BetsPage } from "./pages/BetsPage";
import { MatchupPage } from "./pages/MatchupPage";
import { NbaSlatePage } from "./pages/NbaSlatePage";
import { OpsPage } from "./pages/OpsPage";
import { SlatePage } from "./pages/SlatePage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <OwnerBar />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<Navigate replace to="/nfl" />} />
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
          <Route path="/nba" element={<NbaSlatePage />} />
          <Route path="/nba/:date" element={<NbaSlatePage />} />
          <Route path="/nba/report/:date" element={<NbaReportPage />} />
          <Route path="/game/:id" element={<MatchupPage />} />
          <Route path="/bets" element={<BetsPage />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/odds" element={<App />} />
          <Route path="*" element={<Navigate replace to="/nfl" />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
