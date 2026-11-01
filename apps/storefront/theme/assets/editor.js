/* Native Gutenberg dynamic blocks; the owner can move sections in the Site Editor. */
(() => {
 const labels={'preview-notice':'Aviso de prévia','hero-cta':'Ação da home','selection':'Seleção aprovada','categories':'Categorias','demo-studies':'Estudos visuais (admin)','catalog':'Catálogo WooCommerce','product':'Detalhe de produto','locked':'Compra bloqueada','contact':'Atendimento em definição','policy-links':'Políticas e revisão','brand-note':'Informação da marca'};
 for (const [slug,title] of Object.entries(labels)) {
   const name='aic-curadoria/'+slug;
   wp.blocks.registerBlockType(name,{apiVersion:3,title,category:'widgets',icon:'store',supports:{html:false},
     edit:()=>wp.element.createElement(wp.serverSideRender,{block:name}),save:()=>null});
 }
})();
