<script lang="ts">
  /**
   * A file name that, when there is no room, loses characters from its middle and keeps its
   * end; the full name is the tooltip. dir="auto" lets a right-to-left name read in its own
   * direction.
   */
  import { splitFileName } from './lib/fileName';

  export let name: string;

  $: [head, tail] = splitFileName(name);
</script>

<!-- The two parts are separate boxes, which screen readers would read as two words, so
     they hear the name in one piece from a hidden copy instead. -->
<span class="file-name" dir="auto" title={name}
  >{#if tail}<span class="file-name-text">{name}</span><span
      class="file-name-head"
      aria-hidden="true">{head}</span
    ><span class="file-name-tail" aria-hidden="true">{tail}</span>{:else}<span
      class="file-name-head">{head}</span
    >{/if}</span
>

<style>
  /* Rules for spans in the surrounding row (secondary colour, smaller size) must not reach
     the parts: the name looks like the element it sits in. */
  .file-name,
  .file-name-head,
  .file-name-tail {
    color: inherit;
    font-size: inherit;
    /* Spaces at the cut stay visible; a file name never wraps. */
    white-space: pre;
  }
  .file-name {
    position: relative;
    display: inline-flex;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    vertical-align: bottom;
  }
  .file-name-head {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .file-name-tail {
    flex: none;
  }
  .file-name-text {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
</style>
