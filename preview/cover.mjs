export async function makeCover(file, watermark = true) {
  if (!file || !['image/png','image/jpeg'].includes(file.type) || file.size > 10*1024*1024) {
    throw new Error('请选择不超过10MB的 PNG 或 JPEG 图片。');
  }
  const bitmap = await createImageBitmap(file);
  try {
    if (bitmap.width * bitmap.height > 30000000) throw new Error('图片像素过大，请先缩小至3000万像素以内。');
    const canvas = document.createElement('canvas');
    canvas.width=900; canvas.height=500;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle='#ffffff'; ctx.fillRect(0,0,900,500);
    const scale=Math.max(900/bitmap.width,500/bitmap.height);
    const width=bitmap.width*scale, height=bitmap.height*scale;
    ctx.drawImage(bitmap,(900-width)/2,(500-height)/2,width,height);
    if(watermark) {
      ctx.fillStyle='rgba(10,35,55,0.65)'; ctx.fillRect(0,436,900,64);
      ctx.fillStyle='#ffffff'; ctx.font='600 28px system-ui,sans-serif';
      ctx.textAlign='right'; ctx.textBaseline='middle';
      ctx.fillText('LabVIEW伯乐',872,468);
    }
    const url=canvas.toDataURL('image/png');
    const png_base64=url.split(',')[1];
    if(png_base64.length*3/4>2*1024*1024) throw new Error('封面超过2MB，请换一张较简单的图片。');
    return {url,png_base64};
  } finally { bitmap.close(); }
}

export async function makeBodyImage(file, watermark = true) {
  if(!file || !['image/png','image/jpeg'].includes(file.type) || file.size>10*1024*1024) {
    throw new Error('请选择不超过10MB的 PNG/JPEG 正文图片。');
  }
  const bitmap=await createImageBitmap(file);
  try {
    if(bitmap.width*bitmap.height>30000000) throw new Error('图片超过3000万像素，请先缩小。');
    let scale=Math.min(1,900/bitmap.width,1800/bitmap.height);
    const canvas=document.createElement('canvas');
    for(let attempt=0;attempt<12;attempt++) {
      canvas.width=Math.max(1,Math.floor(bitmap.width*scale));
      canvas.height=Math.max(1,Math.floor(bitmap.height*scale));
      const ctx=canvas.getContext('2d');
      ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
      ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
      if(watermark && canvas.width>=160 && canvas.height>=80) {
        const font=Math.max(12,Math.min(28,Math.floor(canvas.width/22)));
        const band=font*2;
        ctx.fillStyle='rgba(10,35,55,0.65)';ctx.fillRect(0,canvas.height-band,canvas.width,band);
        ctx.fillStyle='#fff';ctx.font='600 '+font+'px system-ui,sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
        ctx.fillText('LabVIEW伯乐',canvas.width-12,canvas.height-band/2);
      }
      const url=canvas.toDataURL('image/png');const png_base64=url.split(',')[1];
      if(png_base64.length*3/4<=900000) return {url,png_base64};
      scale*=0.85;
    }
    throw new Error('正文图片无法压缩至限制内，请先缩小或换图。');
  } finally {bitmap.close();}
}
