import MenuBar       from '../components/MenuBar'
import StatusBar     from '../components/StatusBar'
import DeviceList    from '../components/DeviceList'
import TopologyCanvas from '../components/TopologyCanvas'
import RightPanel    from '../components/RightPanel'

export default function MainPage() {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      width: '100vw',
      overflow: 'hidden',
      background: 'var(--bg-base)',
    }}>
      <MenuBar />

      <div style={{
        flex: 1,
        display: 'flex',
        overflow: 'hidden',
        minHeight: 0,
      }}>
        <DeviceList />
        <TopologyCanvas />
        <RightPanel />
      </div>

      <StatusBar />
    </div>
  )
}
