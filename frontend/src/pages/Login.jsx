import React, { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Loader2, ShieldCheck, ArrowRight, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api, formatError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "../components/ui/input-otp";

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [step, setStep] = useState(1);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [devOtp, setDevOtp] = useState("");
  const [loading, setLoading] = useState(false);

  if (user && user.id) {
    return <Navigate to={user.role === "admin" ? "/admin" : "/engineer"} replace />;
  }

  const submitCreds = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      setChallengeId(data.challenge_id);
      setDevOtp(data.dev_otp || "");
      setStep(2);
      toast.success("OTP sent", { description: `Demo OTP: ${data.dev_otp}` });
    } catch (err) {
      toast.error(formatError(err.response?.data?.detail) || "Login failed");
    } finally { setLoading(false); }
  };

  const submitOtp = async (e) => {
    e?.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/verify-otp", {
        email, otp, challenge_id: challengeId,
      });
      login(data.token, data.user);
      toast.success(`Welcome back, ${data.user.name}`);
      nav(data.user.role === "admin" ? "/admin" : "/engineer");
    } catch (err) {
      toast.error(formatError(err.response?.data?.detail) || "Verification failed");
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-5 login-bg">
      {/* Left – Brand */}
      <div className="hidden lg:flex lg:col-span-3 flex-col justify-between p-12 text-white">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded bg-white text-navy grid place-items-center font-black">S</div>
          <div className="font-display font-black text-2xl tracking-tight">ServiceOps</div>
        </div>
        <div className="max-w-lg">
          <div className="text-xs uppercase tracking-[0.3em] text-white/60 mb-4">IT Service Management Platform</div>
          <h1 className="font-display font-black text-5xl xl:text-6xl leading-[1.05] tracking-tight">
            The control room for your field service operation.
          </h1>
          <p className="mt-6 text-white/70 text-lg max-w-md">
            Dispatch engineers, track tickets in real time, generate signed PDF reports — all in one place.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-white/50">
          <ShieldCheck className="w-4 h-4" />
          End-to-end encrypted • JWT + OTP secured
        </div>
      </div>

      {/* Right – Form */}
      <div className="lg:col-span-2 bg-white flex items-center justify-center p-6 sm:p-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-md"
        >
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="w-10 h-10 rounded bg-navy text-white grid place-items-center font-black">S</div>
            <div className="font-display font-black text-xl">ServiceOps</div>
          </div>

          <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
            {step === 1 ? "Step 1 of 2" : "Step 2 of 2"}
          </div>
          <h2 className="font-display font-black text-3xl tracking-tight text-navy mb-2">
            {step === 1 ? "Sign in to ServiceOps" : "Verify your identity"}
          </h2>
          <p className="text-slate-500 mb-8 text-sm">
            {step === 1
              ? "Use your admin or engineer credentials."
              : `We sent a 6-digit code to ${email}.`}
          </p>

          {step === 1 && (
            <form onSubmit={submitCreds} className="space-y-5">
              <div>
                <Label className="text-xs uppercase tracking-wider font-bold text-slate-700">Email</Label>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  required
                  className="mt-1.5 h-12"
                  data-testid="login-email-input"
                />
              </div>
              <div>
                <Label className="text-xs uppercase tracking-wider font-bold text-slate-700">Password</Label>
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="mt-1.5 h-12"
                  data-testid="login-password-input"
                />
              </div>
              <Button
                type="submit"
                disabled={loading}
                className="w-full h-12 bg-navy hover:bg-navy/90 text-white font-bold rounded-md"
                data-testid="login-submit-btn"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <>Continue <ArrowRight className="w-4 h-4 ml-2" /></>}
              </Button>

              <div className="mt-6 p-4 rounded-md bg-slate-50 border border-slate-200">
                <div className="text-xs uppercase tracking-wider font-bold text-slate-600 mb-2">Demo Credentials</div>
                <div className="text-xs text-slate-700 space-y-1 font-mono">
                  <div>admin@serviceops.com / admin123</div>
                  <div>engineer@serviceops.com / engineer123</div>
                </div>
              </div>
            </form>
          )}

          {step === 2 && (
            <form onSubmit={submitOtp} className="space-y-6">
              <div>
                <Label className="text-xs uppercase tracking-wider font-bold text-slate-700 flex items-center gap-2">
                  <KeyRound className="w-3.5 h-3.5" /> 6-digit Code
                </Label>
                <div className="mt-3" data-testid="login-otp-input">
                  <InputOTP maxLength={6} value={otp} onChange={setOtp}>
                    <InputOTPGroup>
                      {[0, 1, 2, 3, 4, 5].map((i) => (
                        <InputOTPSlot key={i} index={i} className="h-12 w-12 text-lg font-mono" />
                      ))}
                    </InputOTPGroup>
                  </InputOTP>
                </div>
                {devOtp && (
                  <div className="mt-3 text-xs text-slate-500">
                    Demo mode: <span className="font-mono font-bold text-navy">{devOtp}</span>
                  </div>
                )}
              </div>
              <Button
                type="submit"
                disabled={loading || otp.length !== 6}
                className="w-full h-12 bg-navy hover:bg-navy/90 text-white font-bold rounded-md"
                data-testid="login-verify-btn"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : "Verify & Sign in"}
              </Button>
              <button
                type="button"
                className="text-xs text-slate-500 hover:text-navy w-full text-center"
                onClick={() => { setStep(1); setOtp(""); }}
              >
                ← Use a different account
              </button>
            </form>
          )}
        </motion.div>
      </div>
    </div>
  );
}
