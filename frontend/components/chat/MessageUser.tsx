import type { UserMessage } from "@/lib/types";

export function MessageUser({ message }: { message: UserMessage }) {
  return (
    <div className="flex items-start justify-end gap-3 pl-12">
      <div className="bg-slate-100 rounded-2xl rounded-tr-sm px-4 py-3 max-w-xl text-slate-900 text-sm leading-relaxed">
        {message.text}
      </div>
      <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-slate-700 flex-shrink-0 text-xs font-semibold">
        BA
      </div>
    </div>
  );
}
