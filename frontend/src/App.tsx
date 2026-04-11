import { Routes, Route } from 'react-router-dom'
import Sidebar from './components/layout/Sidebar'
import Dashboard from './pages/Dashboard'
import Properties from './pages/Properties'
import Companies from './pages/Companies'
import Opportunities from './pages/Opportunities'
import ActivityLog from './pages/ActivityLog'

export default function App() {
  return (
    <div className="flex min-h-screen bg-surface">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/"              element={<Dashboard />} />
          <Route path="/properties"    element={<Properties />} />
          <Route path="/companies"     element={<Companies />} />
          <Route path="/opportunities" element={<Opportunities />} />
          <Route path="/activity"      element={<ActivityLog />} />
        </Routes>
      </main>
    </div>
  )
}
