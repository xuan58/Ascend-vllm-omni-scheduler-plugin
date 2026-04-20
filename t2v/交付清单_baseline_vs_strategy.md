# T2V Baseline vs Strategy 交付清单

## 1) 服务化脚本

- Baseline（纯净镜像，4卡并行）  
  `start_wan22_t2v_pure.sh`
- Strategy（调度策略镜像，4卡并行，DRR参数可配）  
  `start_wan22_t2v_strategy_compare.sh`
- 历史通用脚本（原有）  
  `start_wan22_t2v_balanced.sh`

## 2) Benchmark 脚本

- Baseline（50并发，rps=inf，权重分布一致）  
  `run_t2v_weighted_pure50_inf.py`
- Strategy（50并发，rps=inf，权重分布一致，支持 ingress_bs）  
  `run_t2v_weighted_strategy50_inf.py`

## 3) 结果统计汇总文件

### Baseline

- `t2v_reports/t2v_weighted_pure50_inf_summary_1776420138.json`
- `t2v_reports/t2v_weighted_pure50_inf_reqs_1776420138.csv`
- `t2v_reports/t2v_weighted_pure50_inf_reqs_1776420138.json`

### Strategy

- `t2v_reports/t2v_weighted_strategy50_inf_summary_1776453516.json`
- `t2v_reports/t2v_weighted_strategy50_inf_reqs_1776453516.csv`
- `t2v_reports/t2v_weighted_strategy50_inf_reqs_1776453516.json`

### 指标摘要（来自 summary json）

- Baseline: success_rate=0.98, mean_e2e_s=3165.8631, p99_e2e_s=5747.6314, throughput_rps=0.0084171
- Strategy: success_rate=1.00, mean_e2e_s=2640.3945, p99_e2e_s=5886.8023, throughput_rps=0.0083874

## 4) 插件文件清单

- 核心调度适配器：`server_ingress_plugin/ingress_batch_drr_adapter.py`
- T2V 插件调度服务：`server_ingress_plugin/video_ingress_dispatcher.py`
- T2V 最小接线模板：`server_ingress_plugin/api_server_video_integration_template.md`

可选（T2I路径参考）：
- `server_ingress_plugin/image_ingress_dispatcher.py`
- `server_ingress_plugin/api_server_integration_template.md`

## 5) 插件整合进镜像源码（不改宿主机纯净镜像）

推荐方式：**派生镜像接线**（不改 `quay.io/ascend/vllm-omni:v0.18.0-local-20260411` 本体）

1. 以纯净镜像启动临时容器（只用于制作对照镜像）。
2. 将 `server_ingress_plugin` 目录拷入容器工作目录（如 `/vllm-workspace/vllm-omni/server_ingress_plugin`）。
3. 按 `api_server_video_integration_template.md` 在 `api_server.py` 增加最小 glue code：  
   - import 插件  
   - 懒加载 dispatcher  
   - `/v1/videos` 路径增加插件分支  
4. 保留原路径作为 fallback（插件开关关闭时走原逻辑）。
5. `docker commit` 形成策略对照镜像（例如 `aixuan/vllm-omni:t2v-drr-budgeted-YYYYMMDD`）。

这样可保证：
- 纯净镜像不被修改；
- 插件可开关、可回退；
- 对比实验与 baseline 同口径。

## 6) 启动时可配置参数

### 插件开关/批大小

- `OMNI_VIDEO_INGRESS_BATCH_ENABLE`
- `OMNI_VIDEO_INGRESS_DEFAULT_BS`
- `OMNI_VIDEO_INGRESS_MAX_WAIT_MS`
- `OMNI_VIDEO_INGRESS_STRICT_BATCHING`

### DRR 参数

- `OMNI_VIDEO_INGRESS_Q_BASE`
- `OMNI_VIDEO_INGRESS_AGE_THRESHOLD_MS`
- `OMNI_VIDEO_INGRESS_AGE_BONUS_FACTOR`
- `OMNI_VIDEO_DRR_MAX_QUEUES`
- `OMNI_VIDEO_DRR_QUEUE_BUDGET_OVERRIDES`  
  示例：`{"854x480_3":6,"854x480_4":4,"1280x720_6":2}`

### 适配器兼容参数（通用前缀）

- `OMNI_INGRESS_DRR_MAX_WAIT_MS`
- `OMNI_INGRESS_DRR_STRICT_BATCHING`
- `OMNI_INGRESS_DRR_Q_BASE`
- `OMNI_INGRESS_DRR_AGE_THRESHOLD_MS`
- `OMNI_INGRESS_DRR_AGE_BONUS_FACTOR`
- `OMNI_INGRESS_DRR_QUEUE_BUDGET_OVERRIDES`
- `OMNI_INGRESS_DRR_MAX_QUEUES`

## 7) 复现实验命令（当前已验证）

### Baseline

```bash
cd /docker/aixuan/vllm-omni-v0.18.0-test
bash ./start_wan22_t2v_pure.sh
python3 ./run_t2v_weighted_pure50_inf.py --base-url http://127.0.0.1:18191 --requests 50
```

### Strategy

```bash
cd /docker/aixuan/vllm-omni-v0.18.0-test
bash ./start_wan22_t2v_strategy_compare.sh
python3 ./run_t2v_weighted_strategy50_inf.py --base-url http://127.0.0.1:18191 --requests 50 --ingress-bs 1
```

