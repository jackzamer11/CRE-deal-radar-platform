import { useRef, useState } from 'react'
import { X, Upload, Download, FileSpreadsheet, CheckCircle, AlertTriangle, RefreshCw } from 'lucide-react'
import { uploadPropertiesBulk } from '../api/client'

interface UploadError {
  row: number
  address: string
  reason: string
}

interface UploadResult {
  inserted: number
  updated: number
  skipped: number
  errors: UploadError[]
}

interface Props {
  onClose: () => void
  onDone: () => void
}

export default function BulkUploadModal({ onClose, onDone }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile]           = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult]       = useState<UploadResult | null>(null)
  const [apiError, setApiError]   = useState<string | null>(null)
  const [showErrors, setShowErrors] = useState(false)

  const handleFile = (f: File) => {
    setFile(f)
    setResult(null)
    setApiError(null)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setApiError(null)
    setResult(null)
    try {
      const res = await uploadPropertiesBulk(file)
      setResult(res)
      if (res.inserted > 0 || res.updated > 0) onDone()
    } catch (e: any) {
      setApiError(
        e?.response?.data?.detail
          || e?.message
          || 'Upload failed — check the server log for details.'
      )
    } finally {
      setUploading(false)
    }
  }

  const handleTemplateDownload = () => {
    window.location.href = '/api/properties/bulk-template'
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-surface-card border border-surface-border rounded-2xl shadow-2xl w-full max-w-lg mx-4">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
          <div className="flex items-center gap-2.5">
            <FileSpreadsheet size={18} className="text-accent-blue" />
            <h2 className="text-sm font-bold text-ink-primary">Bulk Upload Properties</h2>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary p-1">
            <X size={17} />
          </button>
        </div>

        <div className="p-6 space-y-5">

          {/* Template download */}
          <button
            onClick={handleTemplateDownload}
            className="flex items-center gap-2 text-xs text-accent-blue hover:underline"
          >
            <Download size={13} />
            Download CSV template (21 columns, example row included)
          </button>

          {/* Drop zone */}
          {!result && (
            <div
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => inputRef.current?.click()}
              className={`
                border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors
                ${file
                  ? 'border-accent-blue/50 bg-accent-blue/5'
                  : 'border-surface-border hover:border-accent-blue/40 hover:bg-surface-muted'}
              `}
            >
              <input
                ref={inputRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                className="hidden"
                onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
              />
              <Upload size={22} className={`mx-auto mb-3 ${file ? 'text-accent-blue' : 'text-ink-muted'}`} />
              {file ? (
                <>
                  <p className="text-sm font-semibold text-ink-primary">{file.name}</p>
                  <p className="text-xs text-ink-muted mt-1">
                    {(file.size / 1024).toFixed(1)} KB · Click to change
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-ink-secondary">Drop a .csv or .xlsx file here</p>
                  <p className="text-xs text-ink-muted mt-1">or click to browse</p>
                </>
              )}
            </div>
          )}

          {/* API-level error */}
          {apiError && (
            <div className="flex items-start gap-2.5 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/25">
              <AlertTriangle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-red-300">{apiError}</p>
            </div>
          )}

          {/* Results */}
          {result && (
            <div className="space-y-3">
              {/* Summary badges */}
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 p-3 text-center">
                  <div className="text-xl font-bold mono text-emerald-400">{result.inserted}</div>
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mt-0.5">Inserted</div>
                </div>
                <div className="rounded-lg bg-accent-blue/10 border border-accent-blue/20 p-3 text-center">
                  <div className="text-xl font-bold mono text-accent-blue">{result.updated}</div>
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mt-0.5">Updated</div>
                </div>
                <div className={`rounded-lg p-3 text-center border ${
                  result.skipped > 0
                    ? 'bg-amber-500/10 border-amber-500/20'
                    : 'bg-surface-muted border-surface-border'
                }`}>
                  <div className={`text-xl font-bold mono ${result.skipped > 0 ? 'text-amber-400' : 'text-ink-muted'}`}>
                    {result.skipped}
                  </div>
                  <div className="text-[10px] text-ink-muted uppercase tracking-wider mt-0.5">Skipped</div>
                </div>
              </div>

              {/* Success message */}
              {(result.inserted > 0 || result.updated > 0) && (
                <div className="flex items-center gap-2 text-xs text-emerald-400">
                  <CheckCircle size={13} />
                  Signals computed automatically for all new and updated properties.
                </div>
              )}

              {/* Skipped rows detail */}
              {result.errors.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowErrors(e => !e)}
                    className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 font-medium"
                  >
                    <AlertTriangle size={12} />
                    {showErrors ? 'Hide' : 'Show'} skipped rows ({result.errors.length})
                  </button>
                  {showErrors && (
                    <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-surface-border">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="bg-surface-muted text-ink-muted">
                            <th className="px-3 py-2 text-left font-medium">Row</th>
                            <th className="px-3 py-2 text-left font-medium">Address</th>
                            <th className="px-3 py-2 text-left font-medium">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.errors.map((e, i) => (
                            <tr key={i} className="border-t border-surface-border">
                              <td className="px-3 py-2 mono text-ink-muted">{e.row}</td>
                              <td className="px-3 py-2 text-ink-secondary max-w-[120px] truncate" title={e.address}>
                                {e.address}
                              </td>
                              <td className="px-3 py-2 text-amber-400">{e.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* Upload another */}
              <button
                onClick={() => { setResult(null); setFile(null) }}
                className="text-xs text-ink-muted hover:text-ink-primary underline"
              >
                Upload another file
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        {!result && (
          <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-border">
            <button
              onClick={onClose}
              className="px-4 py-2 text-xs text-ink-muted hover:text-ink-primary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleUpload}
              disabled={!file || uploading}
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-accent-blue text-white
                         text-xs font-semibold hover:bg-accent-blueDim transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {uploading
                ? <><RefreshCw size={13} className="animate-spin" /> Processing…</>
                : <><Upload size={13} /> Upload</>}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
