import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider, theme } from 'antd'
import zhTW from 'antd/locale/zh_TW'
import AppV2 from './AppV2'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhTW}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          borderRadius: 10,
          colorBgLayout: '#f3f5f8',
          fontFamily: 'Inter, "Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif',
        },
      }}
    >
      <AppV2 />
    </ConfigProvider>
  </React.StrictMode>,
)
