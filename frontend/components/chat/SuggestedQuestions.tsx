// Drawn from what the three indexed acts actually contain, so a first-time
// visitor sees the system working rather than declining. The last is
// deliberately outside the corpus: refusing is a feature, and it should be
// reachable without having to guess a question that triggers it.
const SUGGESTIONS = [
  {
    label: "Punishment for theft",
    question: "What is the punishment for theft?",
  },
  {
    label: "Confession to police",
    question: "Is a confession made to a police officer admissible as evidence?",
  },
  {
    label: "Arrest without warrant",
    question: "When can the police arrest someone without a warrant?",
  },
  {
    label: "Out of scope (shows a refusal)",
    question: "How do I register a private limited company in Bangladesh?",
  },
];

interface SuggestedQuestionsProps {
  onPick: (question: string) => void;
}

export function SuggestedQuestions({ onPick }: SuggestedQuestionsProps) {
  return (
    <div className="space-y-2">
      <p className="text-[11px] font-medium text-slate-400 uppercase tracking-wider px-1">
        Try asking
      </p>
      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion.label}
            type="button"
            className="px-3 py-1.5 rounded-full border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 text-xs text-slate-700 transition-colors"
            onClick={() => onPick(suggestion.question)}
          >
            {suggestion.label}
          </button>
        ))}
      </div>
    </div>
  );
}
