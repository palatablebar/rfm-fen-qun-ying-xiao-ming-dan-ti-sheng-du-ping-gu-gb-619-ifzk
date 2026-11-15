import { api } from '../api.js';
import { ENUMS, audienceTone, can, generationTone, liftTone, reallocationTone } from '../config.js';
import {
  badge,
  drawer,
  errorBox,
  errorMessage,
  esc,
  field,
  fmtLift,
  fmtMoney,
  fmtNum,
  fmtPct,
  kv,
  statStrip,
  table,
  toast,
} from '../ui.js';

const state = {
  campaignId: '',
  budget: '',
  requestId: '', // 幂等键：中断续跑/重试复用，不重复占额
};

function newRequestId() {
  const rand = Array.from({ length: 12 }, () => '0123456789ABCDEF'[Math.floor(Math.random() * 16)]).join('');
  return `REQ-WEB-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-${rand}`;
}

async function loadCityAllocation(campaigns) {
  const campaign = campaigns.find((item) => String(item.id) === String(state.campaignId)) || campaigns[0];
  if (!campaign) return null;
  const budget = Number(state.budget || campaign.budget);
  try {
    const data = await api.get(`/api/campaigns/${campaign.id}/city-allocation`, { budget });
    state.campaignId = String(campaign.id);
    return data;
  } catch (err) {
    toast(errorMessage(err), 'err');
    return null;
  }
}

// ---------------------------------------------------------------------------
// 城市核对表：每城预算配额、人数、增量、未用余额
// ---------------------------------------------------------------------------

function citySummaryTable(preview) {
  const rows = preview.city_summaries || [];
  const transfers = preview.transfers || [];
  return `<div class="table-wrap"><table class="data city-table">
    <thead><tr>
      <th>城市</th><th>配额权重</th><th>预算配额</th><th>投放人数</th><th>券花费</th>
      <th>预计增量</th><th>增量 GMV</th><th>ROI</th><th>调出/调入</th><th>未用余额</th><th>状态</th>
    </tr></thead>
    <tbody>
      ${rows
        .map((row) => {
          const out = transfers
            .filter((t) => (t.funded_by || []).some((f) => f.city === row.city))
            .reduce((sum, t) => sum + (t.funded_by.find((f) => f.city === row.city)?.amount || 0), 0);
          return `<tr data-city="${esc(row.city)}">
            <td><b>${esc(row.city)}</b></td>
            <td class="num">${fmtPct(row.budget_weight, 1)}</td>
            <td class="num">${fmtMoney(row.quota_budget)}</td>
            <td class="num">${fmtNum(row.audience)}<div class="mini">调入 ${fmtNum(row.received_members)} 人</div></td>
            <td class="num">${fmtMoney(row.spend)}</td>
            <td class="num">${fmtNum(row.inc_orders)}</td>
            <td class="num">${fmtMoney(row.inc_gmv, 0)}</td>
            <td class="num">${Number(row.roi).toFixed(2)}</td>
            <td class="num"><div class="mini">调出 ${fmtMoney(out)}<br>调入 ${fmtMoney(row.received_budget)}</div></td>
            <td class="num"><b>${fmtMoney(row.unused_budget)}</b></td>
            <td>${badge(
              row.received_budget > 0 ? 'info' : out > 0 ? 'warn' : row.unused_budget > 0 ? 'low' : 'ok',
              esc(row.status_label),
            )}</td>
          </tr>`;
        })
        .join('')}
    </tbody>
    <tfoot><tr>
      <td><b>合计</b></td>
      <td></td>
      <td class="num"><b>${fmtMoney(preview.totals.budget)}</b></td>
      <td class="num"><b>${fmtNum(preview.totals.audience)}</b></td>
      <td class="num"><b>${fmtMoney(preview.totals.spend)}</b></td>
      <td class="num"><b>${fmtNum(preview.totals.inc_orders)}</b></td>
      <td class="num"><b>${fmtMoney(preview.totals.inc_gmv, 0)}</b></td>
      <td class="num"><b>${Number(preview.totals.roi).toFixed(2)}</b></td>
      <td class="num mini">${preview.totals.transfers} 笔调剂</td>
      <td class="num"><b>${fmtMoney(preview.totals.idle_budget)}</b></td>
      <td></td>
    </tr></tfoot>
  </table></div>`;
}

function cityDetailBlocks(preview) {
  return (preview.cities || [])
    .map((city) => {
      const allocated = (city.segments || []).filter((row) => row.status === 'allocated');
      if (!allocated.length && !(city.segments || []).length) {
        return `<details class="city-detail"><summary><b>${esc(city.city)}</b>
          <span class="mini">本城暂无可投人群 · 配额 ${fmtMoney(city.quota_budget)} 全额闲置</span></summary>
          <div class="note">该城暂无已评分会员或全部 lift≤1，配额保留，未被其它城市在试算阶段占用。</div></details>`;
      }
      return `<details class="city-detail" ${city.city === '上海' ? 'open' : ''}>
        <summary><b>${esc(city.city)}</b>
          <span class="mini">本城大盘率 ${fmtPct(city.global_rate)} · 配额 ${fmtMoney(city.quota_budget)} ·
          投 ${fmtNum(city.totals.audience)} 人 · 闲置 ${fmtMoney(city.totals.idle_budget)}</span></summary>
        <div class="table-wrap"><table class="data">
          <thead><tr><th>分群</th><th>本城 lift</th><th>人均成本</th><th>群人数</th><th>投放</th>
            <th>预算</th><th>增量</th><th>ROI</th><th>调剂</th></tr></thead>
          <tbody>${(city.segments || [])
            .map(
              (row) => `<tr>
                <td>${esc(row.segment_name)}<div class="mini">${esc(row.reason || '')}</div></td>
                <td class="num"><span class="lift-pill ${liftTone(row.lift)}">${fmtLift(row.lift)}</span></td>
                <td class="num">${fmtMoney(row.unit_cost)}</td>
                <td class="num">${fmtNum(row.member_count)}</td>
                <td class="num">${fmtNum(row.allocated)}</td>
                <td class="num">${fmtMoney(row.budget)}</td>
                <td class="num">${fmtNum(row.inc_orders)}</td>
                <td class="num">${Number(row.roi || 0).toFixed(2)}</td>
                <td>${
                  row.reallocation
                    ? badge(reallocationTone(row.reallocation), ENUMS.reallocationStatus[row.reallocation] || row.reallocation)
                    : '<span class="muted">—</span>'
                }</td>
              </tr>`,
            )
            .join('')}</tbody>
        </table></div>
      </details>`;
    })
    .join('');
}

function transferTable(preview) {
  const transfers = preview.transfers || [];
  if (!transfers.length) {
    return '<div class="note">本次各城配额基本用尽，没有发生跨城调剂；人不够城市的未用余额已在城市表逐城列出。</div>';
  }
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>承接城市</th><th>分群</th><th>承接人数</th><th>调入预算</th><th>资金来源（发布前调剂）</th></tr></thead>
    <tbody>${transfers
      .map(
        (t) => `<tr>
          <td><b>${esc(t.city)}</b></td>
          <td>${esc(t.segment_name)}</td>
          <td class="num">${fmtNum(t.received_members)}</td>
          <td class="num">${fmtMoney(t.received_budget)}</td>
          <td class="mini mono">${(t.funded_by || []).map((f) => `${esc(f.city)} ${fmtMoney(f.amount)}`).join(' ＋ ')}</td>
        </tr>`,
      )
      .join('')}</tbody>
  </table></div>`;
}

// ---------------------------------------------------------------------------
// 生成任务与历史名单
// ---------------------------------------------------------------------------

function generationPanel(generations) {
  const items = generations.items || [];
  if (!items.length) return '';
  return `<section class="panel">
    <div class="panel-head"><h2>生成任务（可中断续跑）</h2>
      <span class="meta">同 request_id 重试不重复占额 · 发布一次性切换，失败保留旧版</span></div>
    ${table({
      rows: items,
      columns: [
        { key: 'request_id', label: '请求号', className: 'mono', render: (row) => esc(row.request_id) },
        {
          key: 'status',
          label: '状态',
          render: (row) => badge(generationTone(row.status), row.status_label),
        },
        { key: 'attempts', label: '尝试次数', className: 'num' },
        { key: 'budget', label: '预算', className: 'num', render: (row) => fmtMoney(row.budget) },
        {
          key: 'city_progress',
          label: '城市进度',
          render: (row) => {
            const done = row.city_progress.filter((c) => c.status !== 'pending').length;
            return `${fmtNum(done)}/${fmtNum(row.city_progress.length)} 城`;
          },
        },
        {
          key: 'actions',
          label: '操作',
          render: (row) =>
            row.status === 'failed'
              ? `<button class="btn sm ghost" data-resume="${row.id}">续跑</button>`
              : row.status === 'ready'
                ? `<button class="btn sm accent" data-publish="${row.id}">发布正式名单</button>`
                : row.status === 'published'
                  ? `<span class="mini muted">已生成名单 #${row.audience_id}</span>`
                  : '<span class="mini muted">进行中…</span>',
        },
      ],
      empty: { title: '还没有生成任务', hint: '点「生成名单（城市配额）」开始八城独立试算。' },
    })}
  </section>`;
}

function historyTable(audiences) {
  return table({
    rows: audiences,
    columns: [
      { key: 'audience_no', label: '名单号', className: 'mono' },
      { key: 'campaign_name', label: '活动' },
      {
        key: 'version',
        label: '版本',
        render: (row) =>
          row.current_version
            ? badge('ok', `v${row.version} 正式版`)
            : `<span class="mini muted">v${row.version} 历史版</span>`,
      },
      { key: 'status', label: '状态', render: (row) => badge(audienceTone(row.status), row.status_label) },
      { key: 'member_count', label: '人数', className: 'num', render: (row) => fmtNum(row.member_count) },
      { key: 'budget', label: '预算', className: 'num', render: (row) => fmtMoney(row.budget) },
      { key: 'spend', label: '实花', className: 'num', render: (row) => fmtMoney(row.spend) },
      { key: 'inc_orders', label: '预计增量', className: 'num', render: (row) => fmtNum(row.inc_orders) },
      { key: 'roi', label: 'ROI', className: 'num', render: (row) => Number(row.roi).toFixed(2) },
      { key: 'operator_email', label: '生成人', render: (row) => `<span class="mono muted">${esc(row.operator_email)}</span>` },
    ],
    empty: { title: '还没有正式名单', hint: '生成任务发布后会出现在这里。' },
  });
}

function openAudience(view, ctx, audience, campaigns) {
  const rows = audience.allocation || [];
  const cityRows = audience.city_allocation || [];
  const canConfirm = can(ctx.user.role, 'audience:confirm');
  const { root, close } = drawer({
    title: `名单 ${audience.audience_no}（v${audience.version}${audience.current_version ? ' · 正式版' : ' · 历史版'}）`,
    subtitle: `${audience.campaign_name || ''} · ${audience.status_label}`,
    body: `${kv([
      ['活动', esc(audience.campaign_name || '—')],
      ['名单人数', fmtNum(audience.member_count)],
      ['预算 / 实花', `${fmtMoney(audience.budget)} / ${fmtMoney(audience.spend)}`],
      ['未用余额', fmtMoney(audience.budget - audience.spend)],
      ['预计增量单量', fmtNum(audience.inc_orders)],
      ['增量 GMV', fmtMoney(audience.inc_gmv)],
      ['整体 ROI', `<b>${Number(audience.roi).toFixed(2)}</b>`],
      ['大盘购买率', fmtPct(audience.global_rate)],
      ['替代旧版', audience.supersedes_id ? `#${audience.supersedes_id}` : '首版'],
      ['生成人 / 时间', `${esc(audience.operator_email)} · ${esc((audience.created_at || '').slice(0, 16).replace('T', ' '))}`],
    ])}
    <h3 style="margin:14px 0 6px;font-size:0.95rem">城市核对（预算 / 人数 / 增量 / 未用余额）</h3>
    <div class="table-wrap"><table class="data" style="min-width:0">
      <thead><tr><th>城市</th><th>配额</th><th>人数</th><th>花费</th><th>增量</th><th>未用余额</th></tr></thead>
      <tbody>${cityRows
        .map(
          (row) => `<tr><td>${esc(row.city)}</td><td class="num">${fmtMoney(row.quota_budget)}</td>
            <td class="num">${fmtNum(row.audience)}</td><td class="num">${fmtMoney(row.spend)}</td>
            <td class="num">${fmtNum(row.inc_orders)}</td><td class="num">${fmtMoney(row.unused_budget)}</td></tr>`,
        )
        .join('')}</tbody></table></div>
    <h3 style="margin:14px 0 6px;font-size:0.95rem">分群预算分配（八城汇总）</h3>
    <div class="table-wrap"><table class="data" style="min-width:0">
      <thead><tr><th>分群</th><th>lift</th><th>人数</th><th>预算</th><th>增量</th><th>ROI</th></tr></thead>
      <tbody>${rows
        .filter((row) => row.status === 'allocated')
        .map(
          (row) => `<tr><td>${esc(row.segment_name)}</td><td class="mono">${fmtLift(row.lift)}</td>
            <td class="num">${fmtNum(row.allocated)}</td><td class="num">${fmtMoney(row.budget)}</td>
            <td class="num">${fmtNum(row.inc_orders)}</td><td class="num">${Number(row.roi).toFixed(2)}</td></tr>`,
        )
        .join('')}</tbody></table></div>
    <div class="note" style="margin-top:10px">${esc(audience.note || '')}</div>`,
    footer: canConfirm
      ? `<div class="rule-chipline">
          <button class="btn accent" data-confirm="confirmed">确认名单</button>
          <button class="btn ghost" data-confirm="pushed">标记已推送券平台</button>
          <button class="btn sm ghost" data-download>导出名单明细</button>
        </div>`
      : '<span class="mono muted">只读角色可以查看名单，但不能确认或导出</span>',
  });

  root.querySelectorAll('[data-confirm]').forEach((node) => {
    node.addEventListener('click', async () => {
      try {
        await api.patch(`/api/audiences/${audience.id}/review`, { status: node.dataset.confirm });
        toast('名单状态已更新');
        close();
        await audiencesPage.render(view, ctx);
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  });
  root.querySelector('[data-download]')?.addEventListener('click', async () => {
    try {
      await api.download(`/api/exports/audiences.csv?audience_id=${audience.id}`, `${audience.audience_no}.csv`);
      toast('已开始下载名单明细');
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
  void campaigns;
}

export const audiencesPage = {
  async render(view, ctx) {
    let campaigns;
    let history;
    let generations;
    try {
      [campaigns, history, generations] = await Promise.all([
        api.get('/api/campaigns'),
        api.get('/api/audiences', { limit: 20, current_only: false }),
        api.get('/api/generations', { limit: 10 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => audiencesPage.render(view, ctx));
      return;
    }
    if (!campaigns.items.length) {
      view.innerHTML = errorBox('还没有活动，请先执行 seed 或由管理员创建活动。');
      return;
    }
    if (!state.campaignId) state.campaignId = String(campaigns.items[0].id);
    const preview = await loadCityAllocation(campaigns.items);
    const campaign = campaigns.items.find((item) => String(item.id) === String(state.campaignId)) || campaigns.items[0];
    const admin = can(ctx.user.role, 'audience:generate');
    const totals = preview ? preview.totals : null;

    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>名单与提升度 · 八城独立配额</h2>
          <span class="meta">${campaign.name} · ${campaign.status_label} · 券口径 ${campaign.channel}</span>
        </div>
        <div class="panel-body" style="display:grid;gap:12px">
          <div class="filter-bar">
            ${field({
              label: '活动',
              name: 'campaign_id',
              value: state.campaignId,
              options: campaigns.items.map((item) => ({
                value: item.id,
                label: `${item.name}（预算 ${fmtMoney(item.budget, 0)}）`,
              })),
            })}
            ${field({
              label: '本次预算（元）',
              name: 'budget',
              type: 'number',
              value: state.budget || campaign.budget,
              attrs: 'step="100" min="1"',
            })}
            <button class="btn ghost" id="preview">重新试算</button>
            ${
              admin
                ? '<button class="btn accent" id="generate">生成名单（城市配额）</button>'
                : '<span class="note">只读 / 专员角色只能试算与确认，不能生成</span>'
            }
          </div>
          ${
            preview
              ? `<div class="note">
                  算法 C：每城用<b>本地大盘购买率</b>算 lift，上海的高响应不再抬高成都、武汉基线；
                  各城只花自己的预算配额，城市间互不抢占；人不够的城市只记闲置，<b>发布前</b>才统一调剂。
                  ${esc(preview.log)}
                </div>`
              : ''
          }
        </div>
      </section>

      ${
        preview
          ? `<section class="panel">
               <div class="panel-head"><h2>城市核对表</h2>
                 <span class="meta">预算 · 人数 · 增量 · 未用余额，按城核对（与导出同源）</span></div>
               ${citySummaryTable(preview)}
             </section>
             <section class="panel">
               <div class="panel-head"><h2>各城分群试算明细</h2><span class="meta">本地 lift / 本地配额贪心</span></div>
               <div class="panel-body" style="display:grid;gap:8px">${cityDetailBlocks(preview)}</div>
             </section>
             <section class="panel">
               <div class="panel-head"><h2>发布前跨城调剂</h2><span class="meta">只在发布前发生，试算阶段配额不动</span></div>
               <div class="panel-body">${transferTable(preview)}</div>
             </section>`
          : '<section class="panel"><div class="panel-body"><div class="empty"><div class="mark">没有可投分群</div><p>提高预算或先重算分群。</p></div></div></section>'
      }

      ${
        totals
          ? statStrip([
              { label: '名单人数', value: fmtNum(totals.audience), hint: `八城投放合计` },
              { label: '券预算花费', value: fmtMoney(totals.spend), hint: `总预算 ${fmtMoney(totals.budget)}` },
              { label: '预计增量单量', value: fmtNum(totals.inc_orders), hint: '核心盘全效 + 溢出折减' },
              { label: '未用余额', value: fmtMoney(totals.idle_budget), hint: `调剂 ${totals.transfers} 笔后剩余` },
            ])
          : ''
      }

      ${admin ? generationPanel(generations) : ''}

      <section class="panel">
        <div class="panel-head">
          <h2>名单版本</h2>
          <span class="meta">${fmtNum(history.total)} 份 · 正式版与历史版都保留，点击查看城市核对</span>
        </div>
        <div id="audience-history">${historyTable(history.items)}</div>
      </section>`;

    const campaignField = view.querySelector('[name=campaign_id]');
    const budgetField = view.querySelector('[name=budget]');
    campaignField?.addEventListener('change', () => {
      state.campaignId = campaignField.value;
      state.budget = '';
      audiencesPage.render(view, ctx);
    });
    view.querySelector('#preview')?.addEventListener('click', () => {
      state.budget = budgetField.value;
      audiencesPage.render(view, ctx);
    });

    view.querySelector('#generate')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      // 同一轮操作固定 request_id：网络重试/续跑都复用，后端不会重复占额
      if (!state.requestId) state.requestId = newRequestId();
      button.disabled = true;
      button.textContent = '八城试算中…';
      try {
        const generation = await api.post('/api/generations', {
          campaign_id: Number(state.campaignId),
          budget: Number(budgetField.value || campaign.budget),
          request_id: state.requestId,
        });
        if (generation.status === 'ready') {
          toast(`八城试算完成（请求号 ${generation.request_id}），可在下方发布正式名单`);
        } else if (generation.status === 'failed') {
          toast(`生成中断：${generation.failure_reason || '未知原因'}，可点续跑`, 'err');
        }
        state.requestId = '';
        await audiencesPage.render(view, ctx);
      } catch (err) {
        toast(errorMessage(err), 'err');
        button.disabled = false;
        button.textContent = '生成名单（城市配额）';
      }
    });

    view.querySelectorAll('[data-resume]').forEach((node) => {
      node.addEventListener('click', async () => {
        node.disabled = true;
        try {
          const generation = await api.post(`/api/generations/${node.dataset.resume}/resume`, {});
          toast(generation.status === 'ready' ? '续跑完成，已可发布' : '续跑后仍未完成，请再次续跑');
          await audiencesPage.render(view, ctx);
        } catch (err) {
          toast(errorMessage(err), 'err');
          node.disabled = false;
        }
      });
    });

    view.querySelectorAll('[data-publish]').forEach((node) => {
      node.addEventListener('click', async () => {
        node.disabled = true;
        try {
          const audience = await api.post(`/api/generations/${node.dataset.publish}/publish`, {});
          toast(`已一次性切换为正式名单 ${audience.audience_no}（v${audience.version}）：${audience.member_count} 人`);
          await audiencesPage.render(view, ctx);
        } catch (err) {
          toast(`发布失败，旧版正式名单保留：${errorMessage(err)}`, 'err');
          node.disabled = false;
        }
      });
    });

    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      const audience = history.items.find((item) => String(item.id) === row.dataset.id);
      if (audience) {
        row.addEventListener('click', () => openAudience(view, ctx, audience, campaigns.items));
      }
    });
  },
};

export default audiencesPage;
