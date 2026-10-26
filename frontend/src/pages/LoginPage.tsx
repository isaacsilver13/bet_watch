import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { loginOwner } from "../lib/owner";

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: loginOwner,
    onSuccess: async () => {
      setPassword("");
      await queryClient.invalidateQueries({ queryKey: ["owner-session"] });
      navigate("/nba", { replace: true });
    },
  });

  return (
    <main className="owner-login">
      <form onSubmit={(event) => { event.preventDefault(); login.mutate(password); }}>
        <p className="eyebrow">Private market desk</p>
        <h1>Sign in</h1>
        <p>Use the owner password to see games, reports, and your bet log.</p>
        <label htmlFor="owner-password">Password</label>
        <input autoComplete="current-password" id="owner-password" onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
        {login.error && <p className="notice error-notice" role="alert">{login.error.message}</p>}
        <button className="refresh-button" disabled={login.isPending} type="submit">
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
