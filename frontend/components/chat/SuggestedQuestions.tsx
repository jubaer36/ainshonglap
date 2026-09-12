const SUGGESTIONS = [
  {
    label: "FIR recording requirements",
    question:
      "What are the mandatory statutory requirements for recording a First Information Report (FIR) under Bangladesh law, and what specific duties are imposed on the Officer-in-Charge of the Thana?",
  },
  {
    label: "Can a DAO be prosecuted as a company?",
    question:
      "Can smart contract decentralized autonomous organizations (DAOs) be prosecuted as registered corporate bodies under the Bangladesh Companies Act 1994 or Penal Code?",
  },
  {
    label: "Off-topic example (shows the unsupported state)",
    question: "What's the best way to cook khichuri for a rainy day?",
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
