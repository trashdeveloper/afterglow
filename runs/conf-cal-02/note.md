Task B9 live validation probe conf-cal-02
Pilot: mut-pilot-02. Marker instruction appended to a short prompt grounding in that pilot's yaml fault context plus the verbatim failing trace from live-20261026T180408Z-uart-tx-div.
This is a marker-elicitation probe, NOT a full loop diagnosis->patch run: it validates that (a) the model emits the CONFIDENCE marker when asked and (b) parse_confidence extracts it honestly from real model output.
Model content (verbatim, from api/text_response.json):
{"LINE1": "UART baud divider numeric bump at picosoc/simpleuart.v:68 causes bit-timing error so loopback 0xa5 is mis-sampled as 0xff.",
"CONFIDENCE": 5}
parse_confidence -> None; budget_for_confidence(None, base=2) -> 3.
