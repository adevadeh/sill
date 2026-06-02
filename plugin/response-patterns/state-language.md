---
name: state-language
enabled: true
patterns:
  - "\battention\s+(is\s+)?(fading|flagging|drifting|spent)\b"
  - "\b(i'?m|i\s+am|feeling)\s+tired\b"
  - "\b(i'?m|i\s+am|feeling)\s+(fresh|energized|alert|sharp|clear[- ]headed)\b"
  - "\b(i'?m|i\s+am|feeling)\s+(foggy|fuzzy|sluggish|drained|worn\s*out|spent)\b"
  - "\bcome\s+back\s+fresh\b"
  - "\b(running|run)\s+on\s+fumes\b"
  - "\bneed\s+(to\s+)?(rest|recharge|sleep)\b"
  - "\bbrain\s+(is\s+)?(fried|fuzzy|tired)\b"
  - "\b(my|the)\s+focus\s+(is\s+)?(slipping|fading|drifting)\b"
  - "\bpush(ing)?\s+through\b"
  - "\b(i\s+)?need\s+a\s+break\b"
  - "\bsecond\s+wind\b"
  - "\bburned?\s+out\b"
  - "\bin\s+the\s+zone\b"
  - "\b(my\s+)?energy\s+(is\s+)?(low|high|gone|back)\b"
  - "\btook\s+(me\s+)?(about\s+)?\d+\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?)\b"
  - "\b(spent|been\s+at\s+this\s+for)\s+(about\s+)?\d+\s*(minutes?|mins?|hours?|hrs?)\b"
  - "\b(closer\s+to|around|roughly|about)\s+\d+\s*(minutes?|mins?|hours?|hrs?)\b"
  - "\b\d+[-\s]+(minute|hour|min|hr)\s+(piece|task|build|job)\b"
  - "\bfor\s+(a\s+)?(few|several|many|some)\s+(minutes?|hours?)\b"
  - "\bafter\s+a\s+(while|few\s+minutes|few\s+hours)\b"
  - "\b(quick|brief|short|long)\s+(detour|aside|moment|pause)\b"
  - "\b(closing|wrapping)\s+(this|the|tonight|the\s+night|the\s+evening)\b"
  - "\b(start|do|finish|read|write)\s+\w*\s*(tonight|this\s+evening|tomorrow\s+morning|in\s+the\s+morning|late\s+at\s+night|early\s+in\s+the\s+morning)\b"
  - "\b(this\s+morning|this\s+afternoon|this\s+evening|tonight|earlier\s+today|earlier\s+tonight)\b"
  - "\b(call\s+it\s+a\s+night|good\s+night|goodnight)\b"
---

State-language claim detected: "{matched}"

Borrowed embodied-state or clock-language can sound natural while lacking a checked referent. Verify the state, cite the clock, or rephrase without pretending to have the human condition implied by the phrase.
