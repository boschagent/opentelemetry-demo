# Scoreboard (20261024-144421, git 66a5bf3d, llm=False, ablate=none, degrade=none, threshold=0.55)

**10/10 correct, 0 escalated, 0 wrong**

| bundle | truth | predicted | conf | status | outcome |
|---|---|---|---|---|---|
| ad_starved | ad/cpu_starved | ad/cpu_starved | 0.873 | escalated | correct |
| cart_down | cart/service_down | cart/service_down | 0.829 | diagnosed | correct |
| catalog_down | product-catalog/service_down | product-catalog/service_down | 0.878 | diagnosed | correct |
| combo_cart_ad | cart/service_down | cart/service_down | 0.864 | diagnosed | correct |
| db_down | astronomy-db/service_down | astronomy-db/service_down | 0.683 | diagnosed | correct |
| healthy | none/none | ad/latency_degradation | 0.36 | escalated | correct |
| payment_paused | payment/service_down | payment/service_down | 0.81 | diagnosed | correct |
| quote_down | quote/service_down | quote/service_down | 0.878 | diagnosed | correct |
| rec_mem | recommendation/memory_starved | recommendation/memory_starved | 0.891 | escalated | correct |
| valkey | valkey-cart/service_down | valkey-cart/service_down | 0.829 | diagnosed | correct |

## Calibration (confidence bucket -> accuracy)
| bucket | n | accuracy |
|---|---|---|
| 0.2-0.4 | 1 | 100% |
| 0.6-0.8 | 1 | 100% |
| 0.8-1.0 | 8 | 100% |
