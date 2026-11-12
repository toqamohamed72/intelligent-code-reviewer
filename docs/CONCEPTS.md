# Core Concepts / المفاهيم الأساسية

## 1. Code as context / الكود كـ context

**English.** A model only knows what is in its prompt. Source code is ideal input because it is bound by a formal
grammar, but it must arrive intact: same indentation, same line breaks. Then we budget it (a model has a context
window), number its lines, and split it at safe boundaries when it does not fit.

**بالمصري.** الموديل مبيعرفش غير اللي في الـ prompt. الكود مدخل ممتاز لأنه ليه قواعد ثابتة، بس لازم يوصل زي ما
هو بالظبط: نفس المسافات ونفس الأسطر. وبعدين بنحسب حجمه، ونرقّم أسطره، ولو كبير بنقسمه عند حدود آمنة (مش جوه function).

## 2. System instruction vs user payload / التعليمات الثابتة مقابل البيانات

**English.** The system instruction fixes behaviour (persona, output contract, "no greetings"). The user message
carries the data (the code). Rules and data are kept apart so text inside the code cannot rewrite the rules.
It reduces prompt-injection risk; it does not remove it, so output is validated too.

**بالمصري.** الـ system instruction بيحدد سلوك الموديل (الشخصية وشكل المخرجات). والـ user message فيه الكود
كبيانات. فصلهم بيخلّي تعليقات جوه الكود مقدرش "تغيّر القواعد". ده بيقلل خطر الـ prompt injection مش بيلغيه،
عشان كده بنتحقق من المخرجات برضه.

## 3. Structured output / المخرجات المنظمة

**English.** Free text is hard to use downstream; a contract is easy. The model must return three exact headers
(`## EXPLANATION`, `## BUG_REPORT`, `## REFACTORED_CODE`). The validator rejects anything else: filler before the
first header, prose in the bug report, two code blocks, an unclosed fence. A rejected answer gets one repair
attempt that quotes the exact problems.

**بالمصري.** النص الحر صعب نعالجه بعد كده، لكن الـ contract سهل. الموديل لازم يرجّع تلات headers بالظبط،
وأي حاجة تانية بتتـرفض: كلام ترحيبي، فقرات في الـ bug report، أكتر من code block، أو fence مش مقفول. والإجابة
المرفوضة بياخد فرصة واحدة يصلّحها بعد ما نقوله المشكلة بالظبط.

## 4. Static analysis + LLM (hybrid) / التحليل الثابت مع الـ LLM

**English.** A parser knows facts for certain (this line is a syntax error); an LLM judges meaning (this loop is
off by one). We use both: static facts go into the prompt, are merged back into the report if the model misses them,
and the refactored code is parsed to check it is syntactically valid. The PDF contrasts the model with regex
linters; in practice the strongest tool is the combination. Note that an LLM does not literally build an AST; it
reasons over the text statistically, which is why we verify instead of trusting.

**بالمصري.** الـ parser بيعرف حاجات أكيدة (السطر ده فيه syntax error)، والـ LLM بيحكم على المعنى (اللوب ده
غلط بواحد). بنستخدم الاتنين: الحقائق الثابتة بتتبعت للموديل، وبتتضاف للتقرير لو هو فوّتها، وبنعمل parse للكود
المُصلَّح نتأكد إنه صحيح نحويًا. وخلي بالك إن الـ LLM مش بيبني AST فعليًا، فمنثقش فيه من غير تحقق.

## 5. Rendering with rich / العرض

**English.** `rich.markdown.Markdown` parses markdown and prints headings, bullets and fenced code with
syntax highlighting chosen from the language tag on the fence. The same canonical markdown is saved as `.md`,
exported to `.html`, and diffed against the original with `difflib`.

**بالمصري.** مكتبة `rich` بتقرا الـ markdown وتطبع العناوين والنقاط والكود بألوان حسب الـ language tag. ونفس
الـ markdown بنحفظه كملف، ونصدّره HTML، ونقارنه بالأصل بـ diff.

## 6. Temperature for analysis / الـ temperature في التحليل

**English.** Intuition says "use a low temperature for deterministic analysis". For Gemini 3, Google recommends
the default (1.0), warning that lower values can loop or degrade; OpenAI reasoning models reject the parameter
unless reasoning effort is `none`. So the tool sends none by default and treats determinism as a pipeline property
(validation, static facts), not a sampling setting. Always check the current docs for your model.

**بالمصري.** الفكرة الشائعة إن temperature صغيرة تدّي ثبات أكتر. لكن مع Gemini 3 جوجل بتنصح بالقيمة الافتراضية
(1.0)، وموديلات OpenAI اللي بتفكّر بترفض الـ parameter ده أحيانًا. فاحنا مبنبعتوش temperature إلا لو طلبتها،
والثبات بنجيبه من التحقق والحقائق الثابتة مش من الـ sampling.
