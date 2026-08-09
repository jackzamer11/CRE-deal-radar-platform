import { Routes, Route } from 'react-router-dom'
import Sidebar from './components/layout/Sidebar'
import Dashboard from './pages/Dashboard'
import Properties from './pages/Properties'
import Companies from './pages/Companies'
// Opportunities tab frozen / hidden from site — page code retained, not deleted.
// import Opportunities from './pages/Opportunities'
import ActivityLog from './pages/ActivityLog'
import Review from './pages/Review'
import Intel from './pages/Intel'

export default function App() {
  return (
    <div className="flex min-h-screen bg-surface">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/"              element={<Dashboard />} />
          <Route path="/properties"    element={<Properties />} />
          <Route path="/companies"     element={<Companies />} />
          {/* Opportunities tab frozen / hidden from site — route retained, not deleted. */}
          {/* <Route path="/opportunities" element={<Opportunities />} /> */}
          <Route path="/review"        element={<Review />} />
          <Route path="/intel"         element={<Intel />} />
          <Route path="/activity"      element={<ActivityLog />} />
        </Routes>
      </main>
    </div>
  )
}
