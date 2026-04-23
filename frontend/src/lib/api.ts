import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('rf_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Redirect to login on 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('rf_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

// ─── Types ────────────────────────────────────────────────────────────────────

export type Platform = 'tiktok' | 'instagram' | 'youtube'
export type JobStatus =
  | 'queued' | 'uploading' | 'processing' | 'posted'
  | 'failed' | 'requires_manual' | 'scheduled' | 'cancelled'
export type PrivacyLevel = 'public' | 'private' | 'unlisted' | 'friends'

export interface Upload {
  id: string
  original_filename: string
  storage_key: string
  thumbnail_key: string | null
  mime_type: string
  file_size_bytes: number
  duration_seconds: number | null
  width: number | null
  height: number | null
  created_at: string
}

export interface PublishJob {
  id: string
  upload_id: string
  platform: Platform
  status: JobStatus
  title: string | null
  caption: string | null
  hashtags: string | null
  privacy: PrivacyLevel | null
  scheduled_for: string | null
  platform_post_id: string | null
  platform_post_url: string | null
  attempt_count: number
  max_attempts: number
  created_at: string
  updated_at: string
}

export interface AuditLog {
  id: string
  publish_job_id: string
  from_status: JobStatus | null
  to_status: JobStatus
  message: string | null
  created_at: string
}

export interface PlatformStatus {
  platform: Platform
  connected: boolean
  account: { platform_username: string | null; connected_at: string } | null
  configured: boolean
  pending_approval: boolean
}

export interface DashboardStats {
  total_uploads: number
  total_jobs: number
  jobs_by_status: Record<string, number>
  recent_jobs: PublishJob[]
}

// ─── API calls ────────────────────────────────────────────────────────────────

export const authApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string }>('/auth/login', { email, password }),
  me: () => api.get('/auth/me'),
}

export const uploadsApi = {
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<Upload>('/uploads/', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  list: () => api.get<Upload[]>('/uploads/'),
  get: (id: string) => api.get<Upload>(`/uploads/${id}`),
}

export const jobsApi = {
  create: (body: Partial<PublishJob> & { upload_id: string; platform: Platform }) =>
    api.post<PublishJob>('/jobs/', body),
  bulkCreate: (upload_id: string, jobs: Array<Partial<PublishJob> & { platform: Platform }>) =>
    api.post<PublishJob[]>('/jobs/bulk', { upload_id, jobs }),
  list: () => api.get<PublishJob[]>('/jobs/'),
  get: (id: string) => api.get<PublishJob>(`/jobs/${id}`),
  retry: (id: string) => api.post<PublishJob>(`/jobs/${id}/retry`),
  cancel: (id: string) => api.post<PublishJob>(`/jobs/${id}/cancel`),
  audit: (id: string) => api.get<AuditLog[]>(`/jobs/${id}/audit`),
  stats: () => api.get<DashboardStats>('/jobs/stats'),
}

export const oauthApi = {
  statuses: () => api.get<PlatformStatus[]>('/oauth/status'),
  connect: (platform: Platform) => {
    window.location.href = `/api/oauth/${platform}/connect`
  },
  disconnect: (platform: Platform) => api.delete(`/oauth/${platform}/disconnect`),
}
