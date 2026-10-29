Task B9 live validation probe conf-cal-01
Pilot: mut-pilot-01. Marker instruction appended to a short prompt grounding in that pilot's yaml fault context plus the verbatim failing trace from live-20261026T174854Z-picosoc-uart-div.
This is a marker-elicitation probe, NOT a full loop diagnosis->patch run: it validates that (a) the model emits the CONFIDENCE marker when asked and (b) parse_confidence extracts it honestly from real model output.
Model content (verbatim, from api/text_response.json):
{"LINE1": "Off-by-one baud divider init in picosoc/simpleuart.v:78 corrupts UART timing causing loopback 0xa5 to be received as 0xe0.", "LINE2": "CONFIDENCE: 5"}
parse_confidence -> 5; budget_for_confidence(5, base=2) -> 2.
