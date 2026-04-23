import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../hooks/useAuthStore'
import { Spinner } from '../components/ui/shared'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const { login, loading } = useAuthStore()
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await login(email, password)
      navigate('/dashboard')
    } catch {
      setError('Invalid email or password.')
    }
  }

  return (
    <div className="min-h-screen bg-forge-bg flex items-center justify-center p-4">
      {/* Subtle background grid */}
      <div
        className="fixed inset-0 opacity-[0.03] pointer-events-none"
        style={{
          backgroundImage: 'linear-gradient(#7c6af7 1px, transparent 1px), linear-gradient(90deg, #7c6af7 1px, transparent 1px)',
          backgroundSize: '60px 60px',
        }}
      />

      <div className="w-full max-w-sm animate-slide-up">
        {/* Logo */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2.5 mb-3">
            <div className="w-9 h-9 rounded-xl bg-forge-accent flex items-center justify-center">
              <span className="text-lg">🎬</span>
            </div>
            <span className="font-display font-extrabold text-2xl text-forge-text tracking-tight">ReelForge</span>
          </div>
          <p className="text-forge-dim text-sm">Admin dashboard sign-in</p>
        </div>

        {/* Card */}
        <div className="card space-y-4 border-forge-border/60">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="label">Email</label>
              <input
                type="email"
                className="input"
                placeholder="admin@example.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                type="password"
                className="input"
                placeholder="••••••••"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
              />
            </div>

            {error && (
              <p className="text-forge-red text-sm bg-forge-red/10 border border-forge-red/20 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-3 flex items-center justify-center gap-2 mt-2"
            >
              {loading ? <><Spinner size={16} /> Signing in…</> : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="text-center text-forge-dim/50 text-xs mt-6 font-mono">
          ReelForge v1.0 · Admin access only
        </p>
      </div>
    </div>
  )
}
