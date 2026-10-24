# Run log (auto-appended by code)

| time | git | command | incident | purpose | result | notes |
|---|---|---|---|---|---|---|
| 2026-10-23T22:46:52 | a419e6f8 | `python -m oncall.drill run cart_down` | INC-20261023-224228-cart_down | practice cart_down | CORRECT: cart/service_down conf=0.919 status=resolved | truth=cart/service_down llm=openai/gpt-oss-120b |
| 2026-10-23T23:07:15 | a419e6f8 | `python -m oncall.drill run valkey` | INC-20261023-230029-valkey | practice valkey | CORRECT: valkey-cart/service_down conf=0.93 status=escalated | truth=valkey-cart/service_down llm=openai/gpt-oss-120b |
| 2026-10-23T23:55:40 | a419e6f8 | `python -m oncall.drill run healthy` | INC-20261023-235527-healthy | practice healthy | CORRECT (no false alarm): cart/latency_degradation conf=0.504 status=escalated | truth=none/none llm=off |
| 2026-10-24T00:06:07 | a419e6f8 | `python -m oncall.drill run cart_down` | INC-20261024-000555-cart_down | practice cart_down | CORRECT: cart/service_down conf=0.89 status=diagnosed | truth=cart/service_down llm=off |
| 2026-10-24T00:19:40 | a419e6f8 | `python -m oncall.drill run valkey` | INC-20261024-001918-valkey | practice valkey | WRONG: cart/app_errors conf=0.666 status=diagnosed | truth=valkey-cart/service_down llm=off |
| 2026-10-24T00:32:34 | a419e6f8 | `python -m oncall.drill run payment_paused` | INC-20261024-003209-payment_paused | practice payment_paused | CORRECT: payment/service_down conf=0.872 status=diagnosed | truth=payment/service_down llm=off |
| 2026-10-24T00:43:47 | a419e6f8 | `python -m oncall.drill run catalog_down` | INC-20261024-004255-catalog_down | practice catalog_down | CORRECT: product-catalog/service_down conf=0.895 status=diagnosed | truth=product-catalog/service_down llm=off |
| 2026-10-24T00:56:52 | a419e6f8 | `python -m oncall.drill run db_down` | INC-20261024-005637-db_down | practice db_down | CORRECT: astronomy-db/service_down conf=0.648 status=diagnosed | truth=astronomy-db/service_down llm=off |
| 2026-10-24T01:09:41 | a419e6f8 | `python -m oncall.drill run quote_down` | INC-20261024-010916-quote_down | practice quote_down | CORRECT: quote/service_down conf=0.88 status=diagnosed | truth=quote/service_down llm=off |
| 2026-10-24T01:22:31 | a419e6f8 | `python -m oncall.drill run rec_mem` | INC-20261024-012209-rec_mem | practice rec_mem | CORRECT: recommendation/service_down conf=0.877 status=diagnosed | truth=recommendation/oom llm=off |
| 2026-10-24T01:36:33 | a419e6f8 | `python -m oncall.drill run ad_starved` | INC-20261024-013608-ad_starved | practice ad_starved | ESCALATED (right component): ad/cpu_starved conf=0.859 status=escalated | truth=ad/cpu_starved llm=off |
| 2026-10-24T01:47:12 | a419e6f8 | `python -m oncall.drill run combo_cart_ad` | INC-20261024-014653-combo_cart_ad | practice combo_cart_ad | CORRECT: cart/service_down conf=0.896 status=diagnosed | truth=cart/service_down llm=off |
| 2026-10-24T01:47:31 | a419e6f8 | `python eval/run_eval.py` | 10 bundles | offline evaluation | 9/10 correct, 0 wrong |  |
| 2026-10-24T10:14:28 | a419e6f8 | `python -m oncall.drill run cart_down` | INC-20261024-101245-cart_down | practice cart_down | CORRECT: cart/service_down conf=0.898 status=resolved | truth=cart/service_down llm=llama-3.3-70b-versatile |
| 2026-10-24T12:06:39 | 66a5bf3d | `python eval/run_eval.py` | 10 bundles | offline evaluation | 10/10 correct, 0 wrong |  |
| 2026-10-24T12:26:23 | 66a5bf3d | `python eval/run_eval.py` | 10 bundles | offline evaluation | 10/10 correct, 0 wrong |  |
| 2026-10-24T14:14:17 | 66a5bf3d | `python eval/run_eval.py` | 10 bundles | offline evaluation | 10/10 correct, 0 wrong |  |
| 2026-10-24T14:44:21 | 66a5bf3d | `python eval/run_eval.py` | 10 bundles | offline evaluation | 10/10 correct, 0 wrong |  |
