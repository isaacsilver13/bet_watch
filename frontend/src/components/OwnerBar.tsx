import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { fetchOwnerSession, logoutOwner } from "../lib/owner";

export function OwnerBar() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["owner-session"], queryFn: fetchOwnerSession });
  const [error, setError] = useState("");
  if (!data?.authenticated) return null;
  const signOut = async () => {
    try {
      setError("");
      await logoutOwner();
      queryClient.clear();
      navigate("/login", { replace: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sign out");
    }
  };
  return <div className="owner-bar"><span>Owner session</span><button onClick={() => void signOut()} type="button">Sign out</button>{error && <span role="alert">{error}</span>}</div>;
}
