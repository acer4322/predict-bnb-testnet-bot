import { Alert, Card, Col, Descriptions, Row, Statistic, Tag, Typography } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text } = Typography
type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

export default function WalletShadowPrivateTakerPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const inference = row(row(service.data).targetTakerPrivateInference)
  const report = row(inference.report)
  const coverage = row(report.coverage)
  const model = row(report.model)
  const test = row(model.untouchedChronologicalTest)
  const validation = row(model.validation)
  const behavior = row(report.behavior)
  const gaps = row(behavior.parentGapSeconds)
  const archive = row(inference.archive)
  const passed = number(inference.testSimilarity) !== null && (number(inference.testSimilarity) ?? 0) >= 0.8

  return (
    <Card title="TARGET_TAKER_PRIVATE_INFERENCE_V1 · 私有策略逆向研究" style={{ marginTop: 12 }}>
      <Alert
        type={passed ? 'success' : 'warning'}
        showIcon
        message={passed ? '完全保留測試已達 80%，可建立新的隔離 forward paper cohort' : '完全保留測試尚未達 80%，目前只保存資料與離線回測'}
        description="舊 Taker V2 已停止新增事件。新模型從目標 parent 標籤學習，但執行推論只讀公開市場資料；訓練、驗證、測試按市場時間順序分離。"
      />
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={12} md={6}><Statistic title="狀態" value={text(inference.status)} /></Col>
        <Col xs={12} md={6}><Statistic title="保留測試相似度" value={pct(test.overallSimilarity)} /></Col>
        <Col xs={12} md={6}><Statistic title="驗證相似度" value={pct(validation.overallSimilarity)} /></Col>
        <Col xs={12} md={6}><Statistic title="市場 / Parent context" value={`${text(coverage.markets, '0')} / ${text(coverage.savedParentContexts, '0')}`} /></Col>
        <Col xs={12} md={6}><Statistic title="因果配對率" value={pct(coverage.pairingRate)} /></Col>
        <Col xs={12} md={6}><Statistic title="負樣本" value={number(coverage.negativeControls) ?? 0} /></Col>
        <Col xs={12} md={6}><Statistic title="測試 Timing F1 ±1s" value={pct(test.timingF1_1s)} /></Col>
        <Col xs={12} md={6}><Statistic title="測試配對方向" value={pct(test.sideAccuracyMatched1s)} /></Col>
        <Col xs={12} md={6}><Statistic title="每場次數相似" value={pct(test.perMarketParentCountSimilarity)} /></Col>
        <Col xs={12} md={6}><Statistic title="每場金額相似" value={pct(test.perMarketNotionalSimilarity)} /></Col>
        <Col xs={12} md={6}><Statistic title="永久封存市場" value={number(archive.markets) ?? 0} /></Col>
        <Col xs={12} md={6}><Statistic title="1 秒封存樣本" value={number(archive.samples) ?? 0} /></Col>
      </Row>
      <Descriptions size="small" column={2} style={{ marginTop: 12 }}>
        <Descriptions.Item label="模型">{text(model.name)}</Descriptions.Item>
        <Descriptions.Item label="特徵數">{text(model.features)}</Descriptions.Item>
        <Descriptions.Item label="驗證選定 threshold">{text(model.thresholdSelectedOnValidation)}</Descriptions.Item>
        <Descriptions.Item label="驗證選定 cooldown">{text(model.cooldownSelectedOnValidationSeconds)} 秒</Descriptions.Item>
        <Descriptions.Item label="Parent 間隔中位數">{text(gaps.median)} 秒</Descriptions.Item>
        <Descriptions.Item label="5 秒內再次交易">{pct(gaps.within5s)}</Descriptions.Item>
        <Descriptions.Item label="執行狀態"><Tag color="gold">OFFLINE ONLY</Tag></Descriptions.Item>
        <Descriptions.Item label="Live orders"><Tag color="success">UNAFFECTED</Tag></Descriptions.Item>
      </Descriptions>
      <Text type="secondary">成交時間是公開 executedAt 的秒級時間；看不到私有掛單時間、未成交單、撤單與對方可能使用的私有資料源，因此相似度不能證明對方真的使用這 34 個輸入。</Text>
    </Card>
  )
}
