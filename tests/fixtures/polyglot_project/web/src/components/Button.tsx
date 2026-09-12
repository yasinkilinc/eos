interface Props {
  label: string;
}

export class Button {
  render(props: Props): string {
    return props.label;
  }
}
