import { useState, useEffect } from 'react'
import Customers from './Customers'
import CustomerDetail from './CustomerDetail'
import Settings from './Settings'
import Reconciliation from './Reconciliation'
import Invoices from './Invoices'
import Activations from './Activations'

const API = 'http://127.0.0.1:8001'

function Dashboard({ sessionData, onLogout }) {
  const [activePage,       setActivePage]       = useState('home')
  const [detailCustomerId, setDetailCustomerId] = useState(null)
  const [detailCustomerName, setDetailCustomerName] = useState('')

  function handleOpenDetail(id, name) {
    setDetailCustomerId(id)
    setDetailCustomerName(name || '')
    setActivePage('customers')
  }

  function handleBackFromDetail() {
    setDetailCustomerId(null)
    setDetailCustomerName('')
  }

  return (
    <div style={styles.container}>

      {/* Sidebar */}
      <div style={styles.sidebar}>

        {/* Logo */}
        <div style={styles.sidebarLogo}>
          <div style={styles.logoIcon}>GB</div>
          <span style={styles.logoText}>GeoBridge</span>
        </div>

        {/* Navigation */}
        <nav style={styles.nav}>
          <NavItem
            icon="&#9632;"
            label="Dashboard"
            active={activePage === 'home'}
            onClick={() => setActivePage('home')}
          />
          <NavItem
            icon="&#9632;"
            label="Customers"
            active={activePage === 'customers'}
            onClick={() => setActivePage('customers')}
          />
          <NavItem
            icon="&#9632;"
            label="Reconciliation"
            active={activePage === 'reconciliation'}
            onClick={() => setActivePage('reconciliation')}
          />
          <NavItem
            icon="&#9632;"
            label="Invoices"
            active={activePage === 'invoices'}
            onClick={() => setActivePage('invoices')}
          />
          <NavItem
            icon="&#9632;"
            label="Activations"
            active={activePage === 'activations'}
            onClick={() => setActivePage('activations')}
          />
          <NavItem
            icon="&#9632;"
            label="Sync Status"
            active={activePage === 'sync'}
            onClick={() => setActivePage('sync')}
          />
          <NavItem
            icon="&#9632;"
            label="Reports"
            active={activePage === 'reports'}
            onClick={() => setActivePage('reports')}
          />
          <NavItem
            icon="&#9632;"
            label="Settings"
            active={activePage === 'settings'}
            onClick={() => setActivePage('settings')}
          />
        </nav>

        {/* User Info & Logout */}
        <div style={styles.sidebarFooter}>
          <div style={styles.userInfo}>
            <div style={styles.userAvatar}>
              {sessionData?.name?.charAt(0).toUpperCase() || 'U'}
            </div>
            <div style={styles.userDetails}>
              <p style={styles.userName}>
                {sessionData?.name || 'User'}
              </p>
              <p style={styles.userRole}>Reseller Admin</p>
            </div>
          </div>
          <button onClick={onLogout} style={styles.logoutBtn}>
            Sign Out
          </button>
        </div>

      </div>

      {/* Main Content */}
      <div style={styles.main}>

        {/* Top Bar */}
        <div style={styles.topbar}>
          <h2 style={styles.pageTitle}>
            {activePage === 'home' && 'Dashboard'}
            {activePage === 'customers' && (detailCustomerId ? detailCustomerName || 'Customer Detail' : 'Customers')}
            {activePage === 'reconciliation' && 'Reconciliation'}
            {activePage === 'invoices' && 'Invoices'}
            {activePage === 'activations' && 'Activations'}
            {activePage === 'sync' && 'Sync Status'}
            {activePage === 'reports' && 'Reports'}
            {activePage === 'settings' && 'Settings'}
          </h2>
          <div style={styles.topbarRight}>
            <div style={styles.statusBadge}>
              Connected to MyAdmin
            </div>
          </div>
        </div>

        {/* Page Content */}
        <div style={styles.content}>
          {activePage === 'home' && <HomePage sessionData={sessionData} />}
          {/* Customers is always mounted so sync state survives navigation.
              CustomerDetail overlays on top when a detail row is clicked. */}
          <div style={{ display: activePage === 'customers' ? 'contents' : 'none' }}>
            {detailCustomerId
              ? <CustomerDetail
                  customerId={detailCustomerId}
                  customerName={detailCustomerName}
                  onBack={handleBackFromDetail}
                />
              : <Customers onDetail={handleOpenDetail} />
            }
          </div>
          {activePage === 'reconciliation' && <Reconciliation />}
          {activePage === 'invoices' && <Invoices />}
          {/* Activations is always mounted so loaded data survives tab switches */}
          <div style={{ display: activePage === 'activations' ? 'contents' : 'none' }}>
            <Activations />
          </div>
          {activePage === 'sync' && <ComingSoon page="Sync Status" />}
          {activePage === 'reports' && <ComingSoon page="Reports" />}
          {activePage === 'settings' && <Settings />}
        </div>

      </div>
    </div>
  )
}

// Home Page
function HomePage({ sessionData }) {
  const [stats, setStats] = useState(null)
  const [statsLoading, setStatsLoading] = useState(true)

  useEffect(() => {
    setStatsLoading(true)
    fetch(`${API}/api/dashboard/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setStats(d) })
      .catch(() => {})
      .finally(() => setStatsLoading(false))
  }, [])

  const fmt = (n) => {
    if (n == null) return '—'
    if (n >= 1000) return n.toLocaleString()
    return String(n)
  }

  const cacheLabel = stats?.cacheAgeHours != null
    ? (stats.cacheAgeHours < 1
        ? `Updated ${Math.round(stats.cacheAgeHours * 60)}m ago`
        : `Updated ${stats.cacheAgeHours.toFixed(1)}h ago`)
    : 'No sync yet'

  return (
    <div style={styles.homePage}>

      {/* Welcome Banner */}
      <div style={styles.welcomeBanner}>
        <h3 style={styles.welcomeTitle}>
          Welcome back, {sessionData?.name || 'User'}
        </h3>
        <p style={styles.welcomeSubtitle}>
          GeoBridge is connected to Geotab MyAdmin and ready to sync.
        </p>
      </div>

      {/* Stats Cards */}
      <div style={styles.statsGrid}>
        <StatCard
          label="Total Devices"
          value={statsLoading ? '…' : fmt(stats?.totalDevices)}
          sub={statsLoading ? 'Loading…' : (stats?.hasCachedData ? cacheLabel : 'Sync to load')}
          color="#3b82f6"
        />
        <StatCard
          label="Total Customers"
          value={statsLoading ? '…' : fmt(stats?.totalCustomers)}
          sub={statsLoading ? 'Loading…' : (stats?.hasCachedData ? cacheLabel : 'Sync to load')}
          color="#8b5cf6"
        />
        <StatCard
          label="Pending Invoices"
          value="—"
          sub="Queued for review"
          color="#f59e0b"
        />
        <StatCard
          label="Synced to QuickBooks"
          value="—"
          sub="Last sync: Never"
          color="#10b981"
        />
      </div>

      {/* Billing type breakdown (only when data is loaded) */}
      {stats?.hasCachedData && stats.billingBreakdown && (
        <div style={styles.breakdownCard}>
          <p style={styles.breakdownTitle}>Billing Type Breakdown</p>
          <div style={styles.breakdownGrid}>
            {Object.entries(stats.billingBreakdown)
              .sort((a, b) => b[1] - a[1])
              .map(([type, count]) => (
                <div key={type} style={styles.breakdownItem}>
                  <span style={styles.breakdownCount}>{count.toLocaleString()}</span>
                  <span style={styles.breakdownLabel}>{type}</span>
                </div>
              ))
            }
          </div>
        </div>
      )}

      {/* Quick Actions */}
      <div style={styles.section}>
        <h4 style={styles.sectionTitle}>Quick Actions</h4>
        <div style={styles.actionsGrid}>
          <ActionCard
            title="Sync from MyAdmin"
            desc="Pull latest device and customer data"
          />
          <ActionCard
            title="Generate Invoices"
            desc="Create invoices for the current billing period"
          />
          <ActionCard
            title="Push to QuickBooks"
            desc="Send queued invoices to QuickBooks"
          />
          <ActionCard
            title="View Reports"
            desc="See billing summaries and trends"
          />
        </div>
      </div>

    </div>
  )
}

// Reusable Components
function NavItem({ icon, label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={active ? styles.navItemActive : styles.navItem}
    >
      <span style={active ? styles.navDotActive : styles.navDot}></span>
      <span>{label}</span>
    </button>
  )
}

function StatCard({ label, value, sub, color }) {
  return (
    <div style={styles.statCard}>
      <div style={{ ...styles.statAccent, background: color }}></div>
      <div style={styles.statInfo}>
        <p style={styles.statLabel}>{label}</p>
        <p style={styles.statValue}>{value}</p>
        <p style={styles.statSub}>{sub}</p>
      </div>
    </div>
  )
}

function ActionCard({ title, desc }) {
  return (
    <div style={styles.actionCard}>
      <h5 style={styles.actionTitle}>{title}</h5>
      <p style={styles.actionDesc}>{desc}</p>
    </div>
  )
}

function ComingSoon({ page }) {
  return (
    <div style={styles.comingSoon}>
      <div style={styles.comingSoonBadge}>Coming Soon</div>
      <h3 style={styles.comingSoonTitle}>{page}</h3>
      <p style={styles.comingSoonText}>
        This section is being built. Check back soon!
      </p>
    </div>
  )
}

// Styles
const styles = {
  container: {
    display: 'flex',
    height: '100vh',
    background: '#0f172a',
    overflow: 'hidden',
  },
  sidebar: {
    width: '220px',
    background: '#1e293b',
    borderRight: '1px solid #334155',
    display: 'flex',
    flexDirection: 'column',
    padding: '24px 0',
    flexShrink: 0,
  },
  sidebarLogo: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '0 20px 24px',
    borderBottom: '1px solid #334155',
    marginBottom: '16px',
  },
  logoIcon: {
    width: '36px',
    height: '36px',
    borderRadius: '8px',
    background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '14px',
    fontWeight: 'bold',
    color: 'white',
    flexShrink: 0,
  },
  logoText: {
    fontSize: '17px',
    fontWeight: '700',
    color: '#f1f5f9',
  },
  nav: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    padding: '0 10px',
    flex: 1,
  },
  navItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '10px 12px',
    borderRadius: '8px',
    border: 'none',
    background: 'transparent',
    color: '#94a3b8',
    fontSize: '14px',
    cursor: 'pointer',
    textAlign: 'left',
    width: '100%',
  },
  navItemActive: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '10px 12px',
    borderRadius: '8px',
    border: 'none',
    background: '#3b82f620',
    color: '#3b82f6',
    fontSize: '14px',
    cursor: 'pointer',
    textAlign: 'left',
    width: '100%',
    fontWeight: '600',
  },
  navDot: {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: '#334155',
    flexShrink: 0,
  },
  navDotActive: {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: '#3b82f6',
    flexShrink: 0,
  },
  sidebarFooter: {
    padding: '16px 20px 0',
    borderTop: '1px solid #334155',
    marginTop: '16px',
  },
  userInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '12px',
  },
  userAvatar: {
    width: '34px',
    height: '34px',
    borderRadius: '50%',
    background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '14px',
    fontWeight: 'bold',
    color: 'white',
    flexShrink: 0,
  },
  userDetails: {
    overflow: 'hidden',
  },
  userName: {
    fontSize: '13px',
    fontWeight: '600',
    color: '#f1f5f9',
    margin: 0,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  userRole: {
    fontSize: '11px',
    color: '#64748b',
    margin: 0,
  },
  logoutBtn: {
    width: '100%',
    padding: '8px',
    borderRadius: '6px',
    border: '1px solid #334155',
    background: 'transparent',
    color: '#94a3b8',
    fontSize: '13px',
    cursor: 'pointer',
  },
  main: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  topbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '18px 32px',
    borderBottom: '1px solid #1e293b',
    background: '#0f172a',
    flexShrink: 0,
  },
  pageTitle: {
    fontSize: '20px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: 0,
  },
  topbarRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  statusBadge: {
    padding: '5px 12px',
    borderRadius: '20px',
    background: '#064e3b',
    color: '#6ee7b7',
    fontSize: '12px',
    fontWeight: '500',
    border: '1px solid #065f46',
  },
  content: {
    flex: 1,
    overflow: 'auto',
    padding: '28px 32px',
  },
  homePage: {
    display: 'flex',
    flexDirection: 'column',
    gap: '28px',
  },
  welcomeBanner: {
    background: 'linear-gradient(135deg, #1e3a5f, #1e293b)',
    borderRadius: '12px',
    padding: '24px 28px',
    border: '1px solid #334155',
  },
  welcomeTitle: {
    fontSize: '19px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: '0 0 6px',
  },
  welcomeSubtitle: {
    fontSize: '14px',
    color: '#94a3b8',
    margin: 0,
  },
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: '16px',
  },
  statCard: {
    background: '#1e293b',
    borderRadius: '12px',
    padding: '20px',
    border: '1px solid #334155',
    display: 'flex',
    gap: '14px',
    alignItems: 'flex-start',
    overflow: 'hidden',
    position: 'relative',
  },
  statAccent: {
    width: '4px',
    borderRadius: '4px',
    alignSelf: 'stretch',
    flexShrink: 0,
  },
  statInfo: {
    flex: 1,
    minWidth: 0,
  },
  statLabel: {
    fontSize: '12px',
    color: '#64748b',
    margin: '0 0 4px',
    fontWeight: '500',
  },
  statValue: {
    fontSize: '26px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: '0 0 2px',
  },
  statSub: {
    fontSize: '11px',
    color: '#475569',
    margin: 0,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: '14px',
  },
  breakdownCard: {
    background: '#1e293b',
    borderRadius: '12px',
    padding: '20px 24px',
    border: '1px solid #334155',
  },
  breakdownTitle: {
    fontSize: '13px',
    fontWeight: '600',
    color: '#64748b',
    margin: '0 0 14px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  breakdownGrid: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '10px',
  },
  breakdownItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 12px',
    background: '#0f172a',
    borderRadius: '8px',
    border: '1px solid #334155',
  },
  breakdownCount: {
    fontSize: '14px',
    fontWeight: '700',
    color: '#f1f5f9',
  },
  breakdownLabel: {
    fontSize: '12px',
    color: '#64748b',
  },
  sectionTitle: {
    fontSize: '15px',
    fontWeight: '600',
    color: '#94a3b8',
    margin: 0,
  },
  actionsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: '16px',
  },
  actionCard: {
    background: '#1e293b',
    borderRadius: '12px',
    padding: '20px',
    border: '1px solid #334155',
    cursor: 'pointer',
  },
  actionTitle: {
    fontSize: '14px',
    fontWeight: '600',
    color: '#f1f5f9',
    margin: '0 0 6px',
  },
  actionDesc: {
    fontSize: '12px',
    color: '#64748b',
    margin: 0,
    lineHeight: '1.5',
  },
  comingSoon: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '400px',
    gap: '14px',
  },
  comingSoonBadge: {
    padding: '6px 16px',
    borderRadius: '20px',
    background: '#1e3a5f',
    color: '#60a5fa',
    fontSize: '12px',
    fontWeight: '600',
    border: '1px solid #2563eb',
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
  },
  comingSoonTitle: {
    fontSize: '22px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: 0,
  },
  comingSoonText: {
    fontSize: '14px',
    color: '#64748b',
    margin: 0,
  },
}

export default Dashboard
