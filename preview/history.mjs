export function upsertHistory(markdown, block) {
  const lines=markdown.split('\n');let fenced=false;const headings=[];
  for(let index=0;index<lines.length;index++) {
    if(lines[index].startsWith('```')) {fenced=!fenced;continue;}
    if(!fenced && /^#{1,2}\s+/.test(lines[index])) headings.push(index);
  }
  const starts=headings.filter(index=>/^##\s+往期回顾\s*$/.test(lines[index]));
  if(starts.length>1) throw new Error('正文中有多个“往期回顾”章节，请先合并。');
  if(!starts.length) return markdown.trimEnd()+'\n\n'+block.trim()+'\n';
  const start=starts[0];const end=headings.find(index=>index>start) ?? lines.length;
  const before=lines.slice(0,start).join('\n').trimEnd();
  const after=lines.slice(end).join('\n').trimStart();
  return (before ? before+'\n\n':'')+block.trim()+'\n'+(after ? '\n'+after:'');
}
