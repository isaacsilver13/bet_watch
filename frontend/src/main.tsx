import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import { MatchupPage } from "./pages/MatchupPage";
import { SlatePage } from "./pages/SlatePage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate replace to="/nfl" />} />
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
          <Route path="/game/:id" element={<MatchupPage />} />
          <Route path="/odds" element={<App />} />
          <Route path="*" element={<Navigate replace to="/nfl" />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
