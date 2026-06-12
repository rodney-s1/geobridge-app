import { useState } from 'react'

function Dashboard({ sessionData, onLogout }) {
  const [activePage, setActivePage] = useState('home')

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
            icon="🏠"
            label="Dashboard"
            active={activePage === 'home'}
            onClick={() => setActivePage('home')}
          />
          <NavItem
            icon="👥"
            label="Customers"
            active={activePage === 'customers'}
            onClick={() => setActivePage('customers')}
          />
          <NavItem
            icon="🧾"
            label="Invoices"
            active={activePage === 'invoices'}
            onClick={() => setActivePage('invoices')}
          />
          <NavItem
            icon="🔄"
            label="Sync Status"
            active={activePage === 'sync'}
            onClick={() => setActivePage('sync')}
          />
          <NavItem
            icon="📊"
            label="Reports"
            active={activePage === 'reports'}
            onClick={() => setActivePage('reports')}
          />
          <NavItem
            icon="⚙️"
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
            {activePage === 'customers' && 'Customers'}
            {activePage === 'invoices' && 'Invoices'}
            {activePage === 'sync' && 'Sync Status'}
            {activePage === 'reports' && 'Reports'}
            {activePage === 'settings' && 'Settings'}
          </h2>
          <div style={styles.topbarRight}>
            <div style={styles.statusBadge}>
              🟢 MyAdmin Connected
            </div>
          </div>
        </div>

        {/* Page Content */}
        <div style={styles.content}>
          {activePage === 'home' && <HomePage sessionData={sessionData} />}
          {activePage === 'customers' && <ComingSoon page="Customers" />}
          {activePage === 'invoices' && <ComingSoon page="Invoices" />}
          {activePage === 'sync' && <ComingSoon page="Sync Status" />}
          {activePage === 'reports' && <ComingSoon page="Reports" />}
          {activePage === 'settings' && <ComingSoon page="Settings" />}
        </div>

      </div>
    </div>
  )
}

// ─── Home Page ────────────────────────────────────────────────
function HomePage({ sessionData }) {
  return (
    <div style={styles.homePage}>

      {/* Welcome Banner */}
      <div style={styles.welcomeBanner}>
        <h3 style={styles.welcomeTitle}>
          Welcome back, {sessionData?.name || 'User'} 👋
        </h3>
        <p style={styles.welcomeSubtitle}>
          GeoBridge is connected to Geotab MyAdmin and ready to sync.
        </p>
      </div>

      {/* Stats Cards */}
      <div style={styles.statsGrid}>
        <StatCard
          icon="📱"
          label="Total Devices"
          value="—"
          sub="Loading from MyAdmin..."
          color="#3b82f6"
        />
        <StatCard
          icon="👥"
          label="Total Customers"
          value="—"
          sub="Loading from MyAdmin..."
          color="#8b5cf6"
        />
        <StatCard
          icon="🧾"
          label="Pending Invoices"
          value="—"
          sub="Queued for review"
          color="#f59e0b"
        />
        <StatCard
          icon="✅"
          label="Synced to QuickBooks"
          value="—"
          sub="Last sync: Never"
          color="#10b981"
        />
      </div>

      {/* Quick Actions */}
      <div style={styles.section}>
        <h4 style={styles.sectionTitle}>Quick Actions</h4>
        <div style={styles.actionsGrid}>
          <ActionCard
            icon="🔄"
            title="Sync from MyAdmin"
            desc="Pull latest device and customer data"
          />
          <ActionCard
            icon="🧾"
            title="Generate Invoices"
            desc="Create invoices for the current billing period"
          />
          <ActionCard
            icon="📤"
            title="Push to QuickBooks"
            desc="Send queued invoices to QuickBooks"
          />
          <ActionCard
            icon="📊"
            title="View Reports"
            desc="See billing summaries and trends"
          />
        </div>
      </div>

    </div>
  )
}

// ─── Reusable Components ──────────────────────────────────────
function NavItem({ icon, label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={active ? styles.navItemActive : styles.navItem}
    >
      <span style={styles.navIcon}>{icon}</span>
      <span>{label}</span>
    </button>
  )
}

function StatCard({ icon, label, value, sub, color }) {
  return (
    <div style={styles.statCard}>
      <div style={{ ...styles.statIcon, background: color + '22', color }}>
        {icon}
      </div>
      <div style={styles.statInfo}>
        <p style={styles.statLabel}>{label}</p>
        <p style={styles.statValue}>{value}</p>
        <p style={styles.statSub}>{sub}</p>
      </div>
    </div>
  )
}

function ActionCard({ icon, title, desc }) {
  return (
    <div style={styles.actionCard}>
      <div style={styles.actionIcon}>{icon}</div>
      <h5 style={styles.actionTitle}>{title}</h5>
      <p style={styles.actionDesc}>{desc}</p>
    </div>
  )
}

function ComingSoon({ page }) {
  return (
    <div style={styles.comingSoon}>
      <div style={styles.comingSoonIcon}>🚧</div>
      <h3 style={styles.comingSoonTitle}>{page}</h3>
      <p style={styles.comingSoonText}>
        This section is being built. Check back soon!
      </p>
    </div>
  )
}

// ─── Styles ───────────────────────────────────────────────────
const styles = {
  container: {
    display: 'flex',
    height: '100vh',
    background: '#0f172a',
    overflow: 'hidden',
  },
  sidebar: {
    width: '240px',
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
    fontSize: '18px',
    fontWeight: '700',
    color: '#f1f5f9',
  },
  nav: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    padding: '0 12px',
    flex: 1,
  },
  navItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
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
    gap: '12px',
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
  navIcon: {
    fontSize: '16px',
    width: '20px',
    textAlign: 'center',
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
    width: '36px',
    height: '36px',
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
    padding: '20px 32px',
    borderBottom: '1px solid #1e293b',
    background: '#0f172a',
    flexShrink: 0,
  },
  pageTitle: {
    fontSize: '22px',
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
    padding: '6px 12px',
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
    padding: '32px',
  },
  homePage: {
    display: 'flex',
    flexDirection: 'column',
    gap: '32px',
  },
  welcomeBanner: {
    background: 'linear-gradient(135deg, #1e3a5f, #1e293b)',
    borderRadius: '12px',
    padding: '28px 32px',
    border: '1px solid #334155',
  },
  welcomeTitle: {
    fontSize: '20px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: '0 0 8px',
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
    gap: '16px',
    alignItems: 'flex-start',
  },
  statIcon: {
    width: '44px',
    height: '44px',
    borderRadius: '10px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '20px',
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
    fontSize: '24px',
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
    gap: '16px',
  },
  sectionTitle: {
    fontSize: '16px',
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
    transition: 'border-color 0.2s',
  },
  actionIcon: {
    fontSize: '28px',
    marginBottom: '12px',
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
    gap: '16px',
  },
  comingSoonIcon: {
    fontSize: '48px',
  },
  comingSoonTitle: {
    fontSize: '24px',
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
